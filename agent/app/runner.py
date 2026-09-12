import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.agents import build_orchestrator_agent
from app.chat_stream import events_from_update
from app.ingestion import IngestMeta, ingest
from app.jobs import ExtractionJob
from app.on_client import OpenNotebookClient
from app.tracing import child_span, new_handler, traced
from app.vision import describe_chart, is_vision_available

logger = logging.getLogger(__name__)

# Below this, a stored full_text is considered usable and the PDF download +
# pypdf fallback is skipped (guards against empty/placeholder values).
MIN_STORED_TEXT_CHARS = 1000

# 图表 PNG 落盘根目录（source_id 子目录），VLM 描述逐图生成
IMAGE_ROOT = Path(__file__).parent.parent / "data" / "images"

# VLM 逐图描述的并发上限：视觉调用慢（thinking 模型），独立于文本并发
VISION_CONCURRENCY = 2

# Progress event callback: async, receives tool_call/tool_result event dicts
OnEvent = Callable[[dict[str, Any]], Awaitable[None]]


async def fetch_source_material(
    client: OpenNotebookClient, job: ExtractionJob
) -> tuple[bytes | None, str | None, str]:
    """取报告原料：PDF 优先（书签/图表结构只有 PDF 有），文本兜底。

    Returns:
        (pdf_bytes, full_text, origin)，origin ∈ {"pdf", "on-parsed",
        "pdf+on-parsed"}，供 job log 与策略链（markdown 策略吃 full_text）。
    """
    source = await client.get_source(job.source_id)
    job.source_title = source.get("title")

    stored = source.get("full_text")
    stored_ok = isinstance(stored, str) and len(stored) >= MIN_STORED_TEXT_CHARS

    pdf_bytes = None
    try:
        pdf_bytes = await client.download_source(job.source_id)
        if not pdf_bytes or pdf_bytes[:4] != b"%PDF":
            pdf_bytes = None
    except Exception as e:  # noqa: BLE001 - PDF 拿不到仍可走纯文本
        job.record(f"PDF download unavailable ({e!r})")

    if pdf_bytes and stored_ok:
        return pdf_bytes, stored, "pdf+on-parsed"
    if pdf_bytes:
        return pdf_bytes, None, "pdf"
    if stored_ok:
        job.record("PDF unavailable, using on-parsed text (no bookmarks/charts)")
        return None, stored, "on-parsed"
    job.record("stored full_text unavailable and PDF download failed")
    return None, "", "empty"


def _format_structure(meta: IngestMeta) -> str:
    """ingest 元信息 → job log 可读摘要（章节/box/图表一目了然）。"""
    chapters = " / ".join(meta.chapters[:8]) + ("…" if len(meta.chapters) > 8 else "")
    return (
        f"strategy={meta.strategy}, chapters={len(meta.chapters)} [{chapters}], "
        f"boxes={meta.box_count}, backmatter={meta.backmatter_count}, "
        f"chart_images={meta.image_count}; chain=[{'; '.join(meta.strategy_chain)}]"
    )


async def describe_section_images(
    sections: list[dict], job: ExtractionJob, on_event: OnEvent | None
) -> int:
    """逐图 VLM 描述，写进 section["image_notes"]（与 images 一一对应）。

    失败降级：单图描述失败记 "（描述不可用）"，不阻塞主流程。
    去重：同一 PNG 只描述一次（跨节挂载的图表页共享描述）。
    """
    imgs = sorted({str(img) for s in sections for img in s.get("images", [])})
    if not imgs:
        return 0
    if not is_vision_available():
        job.record("image notes skipped: VLM not configured")
        for s in sections:
            s["image_notes"] = ["（VLM 未配置，图表描述不可用）"] * len(
                s.get("images", [])
            )
        return 0

    semaphore = asyncio.Semaphore(VISION_CONCURRENCY)
    cache: dict[str, str] = {}

    async def one(i: int, img: str) -> None:
        if on_event:
            await on_event(
                {
                    "type": "tool_call",
                    "id": f"chart-{i}",
                    "name": "chart_reader",
                    "args": {"image": Path(img).name},
                }
            )
        async with semaphore:
            try:
                cache[img] = await describe_chart(img)
            except Exception as e:
                logger.warning("chart describe failed: %s", img, exc_info=True)
                cache[img] = f"（图表描述不可用: {e!r}）"
        if on_event:
            ok = not cache[img].startswith("（")
            await on_event(
                {
                    "type": "tool_result",
                    "id": f"chart-{i}",
                    "name": "chart_reader",
                    "content": cache[img][:300],
                    "status": "success" if ok else "error",
                }
            )

    await asyncio.gather(*(one(i, img) for i, img in enumerate(imgs)))
    for s in sections:
        s["image_notes"] = [
            cache.get(str(img), "（描述不可用）") for img in s.get("images", [])
        ]
    return sum(1 for v in cache.values() if not v.startswith("（"))


async def run_extraction_job(
    job: ExtractionJob, on_event: OnEvent | None = None
) -> None:
    """Fetch a source from ON, run the extraction agent, write insights back.

    With `on_event`, the orchestrator runs via astream(updates) and every
    tool lifecycle update (task dispatch to subagents, submit_insight
    write-backs, chart reading progress) is forwarded as a
    tool_call/tool_result event so callers can stream the extraction
    details live (e.g. into a chat turn).
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
            job.record("fetching source material")
            with child_span("fetch-source-material"):
                pdf_bytes, text, origin = await fetch_source_material(client, job)
            job.record(f"using {origin} material")

            # 数据面：章节结构 + box 剥离 + 图表页渲染
            with child_span("ingest"):
                sections, meta = ingest(
                    pdf_bytes, text, source_id=job.source_id, image_root=IMAGE_ROOT
                )
            job.sections = len(sections)
            job.record(f"ingested {len(sections)} sections ({_format_structure(meta)})")

            # 图表 VLM 描述（摄取期一次性生成，worker 阅读时直接可见）
            with child_span("chart-describe"):
                described = await describe_section_images(sections, job, on_event)
            if meta.image_count:
                job.record(f"chart notes: {described}/{meta.image_count} described")

            agent = build_orchestrator_agent(
                client, sections, job.source_id, job.insight_type,
                source_title=job.source_title or "",
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

            span.update(
                output={"sections": len(sections), "origin": origin, **{
                    k: v for k, v in meta.__dict__.items() if k != "strategy_chain"
                }}
            )

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
