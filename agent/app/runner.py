import time

from langchain_core.messages import HumanMessage

from app.agents import build_orchestrator_agent
from app.extraction import source_bytes_to_text, split_sections
from app.jobs import ExtractionJob
from app.on_client import OpenNotebookClient
from app.tracing import child_span, new_handler, traced

# Below this, a stored full_text is considered usable and the PDF download +
# pypdf fallback is skipped (guards against empty/placeholder values).
MIN_STORED_TEXT_CHARS = 1000


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


async def run_extraction_job(job: ExtractionJob) -> None:
    """Fetch a source from ON, run the extraction agent, write insights back."""
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
            await agent.ainvoke(
                {"messages": [HumanMessage(content="开始按流程提取核心观点。")]},
                config={"callbacks": [new_handler()]},
            )

            span.update(output={"sections": len(sections), "origin": origin})

        job.status = "done"
        job.record("extraction finished")
    except Exception as e:  # noqa: BLE001 - job boundary: record any failure
        job.status = "failed"
        job.error = str(e)
        job.record(f"failed: {e}")
    finally:
        job.finished_at = time.time()
