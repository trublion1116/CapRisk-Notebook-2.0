import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage

from app.agents import build_orchestrator_agent
from app.chat_stream import events_from_update
from app.extraction import source_bytes_to_text, split_sections
from app.jobs import ExtractionJob
from app.on_client import OpenNotebookClient
from app.tracing import child_span, new_handler, traced

logger = logging.getLogger(__name__)

# Below this, a stored full_text is considered usable and the PDF download +
# pypdf fallback is skipped (guards against empty/placeholder values).
MIN_STORED_TEXT_CHARS = 1000

# Progress event callback: async, receives tool_call/tool_result event dicts
OnEvent = Callable[[dict[str, Any]], Awaitable[None]]


async def fetch_source_text(
    client: OpenNotebookClient, job: ExtractionJob
) -> tuple[str, str]:
    """Get the source text: prefer ON's parsed full_text, fall back to PDF.

    Returns (text, origin) where origin describes the path used, for the job
    log ("on-parsed" or "pdf-fallback").
    """
    source = await client.get_source(job.source_id)
    job.source_title = source.get("title")

    stored = source.get("full_text")
    if isinstance(stored, str) and len(stored) >= MIN_STORED_TEXT_CHARS:
        return stored, "on-parsed"

    job.record("stored full_text unavailable, falling back to PDF download")
    data = await client.download_source(job.source_id)
    return source_bytes_to_text(data), "pdf-fallback"


async def run_extraction_job(
    job: ExtractionJob, on_event: OnEvent | None = None
) -> None:
    """Fetch a source from ON, run the extraction agent, write insights back.

    With `on_event`, the orchestrator runs via astream(updates) and every
    tool lifecycle update (task dispatch to subagents, submit_insight
    write-backs) is forwarded as a tool_call/tool_result event so callers
    can stream the extraction details live (e.g. into a chat turn).
    """
    client = OpenNotebookClient()
    try:
        with traced(
            "insight-extraction",
            session_id=job.id,
            tags=["extract"],
            input={
                "source_id": job.source_id,
                "insight_type": job.insight_type,
            },
        ) as span:
            job.trace_id = str(getattr(span, "trace_id", "") or "")
            job.record("fetching source text")
            with child_span("fetch-source-text"):
                text, origin = await fetch_source_text(client, job)
            job.record(f"using {origin} text ({len(text)} chars)")

            with child_span("split-sections", input={"chars": len(text)}):
                sections = split_sections(text)
            job.sections = len(sections)
            job.record(f"split into {len(sections)} sections, agent starting")

            agent = build_orchestrator_agent(
                client, sections, job.source_id, job.insight_type
            )
            callbacks = [new_handler()]
            if on_event is None:
                await agent.ainvoke(
                    {"messages": [HumanMessage(content="开始按流程提取核心观点。")]},
                    config={"callbacks": callbacks},
                )
            else:
                async for mode, chunk in agent.astream(
                    {"messages": [HumanMessage(content="开始按流程提取核心观点。")]},
                    config={"callbacks": callbacks},
                    stream_mode=["updates"],
                ):
                    for event in events_from_update(chunk):
                        await on_event(event)

            span.update(output={"sections": len(sections), "origin": origin})

        job.status = "done"
        job.record("extraction finished")
    except Exception as e:  # job boundary: record any failure
        job.status = "failed"
        # repr (not str): exceptions like asyncio.TimeoutError stringify to
        # "" which hides the failure cause entirely.
        job.error = repr(e)
        job.record(f"failed: {job.error}")
        logger.exception("extraction job %s failed", job.id)
    finally:
        job.finished_at = time.time()
