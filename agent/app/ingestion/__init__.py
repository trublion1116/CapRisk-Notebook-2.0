"""数据面 ingestion 包。

包内模块自动发现：新增切片策略 = 在本包新建一个模块文件，定义
``SegmentationStrategy`` 子类并声明非空 ``name``，此处无需改动——
与 ``app/subagents`` 同一套开闭模式。

统一入口 ``ingest()``：PDF 优先（书签结构最可靠），on-parsed 文本次之，
逐页文本由 pypdfium2 提取（与后续图片渲染共用一个 PDF 句柄）。
"""

import importlib
import io
import logging
import pkgutil
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader

from app.ingestion.base import (
    IngestMeta,
    make_section,
    registered_strategies,
)

logger = logging.getLogger(__name__)

# 导入包内全部模块以触发类注册（base 已导入，跳过避免重复）
for _mod in pkgutil.iter_modules(__path__):
    if _mod.name != "base":
        importlib.import_module(f"{__name__}.{_mod.name}")

# 策略优先级：书签 > markdown 标题 > 正则启发 > 字符兜底。
# 注册表是定义序（模块字母序），这里显式按名排序保证语义稳定。
PRIORITY = ["outline", "markdown-header", "heading-regex", "char-fallback"]


def _extract_pages(pdf_doc: pdfium.PdfDocument) -> list[str]:
    """逐页文本（0-based 下标 = 页码）。空页保留占位，页码对齐书签。"""
    pages = []
    for page in pdf_doc:
        tp = page.get_textpage()
        pages.append(tp.get_text_bounded() or "")
    return pages


def _normalize_chapters(sections: list[dict], meta: IngestMeta) -> None:
    seen: list[str] = []
    for s in sections:
        ch = str(s.get("chapter_title") or "")
        if ch and ch not in seen:
            seen.append(ch)
        if not s.get("title"):
            s["title"] = f"{ch}（续）" if ch else f"第 {s['index']} 节"
    meta.chapters = seen
    meta.box_count = sum(1 for s in sections if s.get("kind") == "box")
    meta.backmatter_count = sum(
        1 for s in sections if s.get("kind") == "backmatter"
    )


def ingest(
    pdf_bytes: bytes | None,
    text: str | None = None,
    *,
    source_id: str = "",
    image_root: str | Path | None = None,
) -> tuple[list[dict], IngestMeta]:
    """报告 → (sections, meta)。

    Args:
        pdf_bytes: 原始 PDF（ON download API），None 时走纯文本路线。
        text: ON 解析的 full_text（可能为 docling markdown），仅无 PDF
            或 PDF 路径全部失败时使用。
        source_id + image_root: 都给定时启用图表页渲染管线（BIS 图表
            是矢量图，只能整页渲染 300DPI PNG，见 images.py）。
    """
    meta = IngestMeta()
    pages: list[str] | None = None
    reader = None
    pdf_doc = None

    if pdf_bytes:
        try:
            # pypdfium2 5.x 需要文件型对象（有 seek），裸 bytes 会 AttributeError
            pdf_doc = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
            pages = _extract_pages(pdf_doc)
            reader = PdfReader(io.BytesIO(pdf_bytes))
        except Exception as e:  # noqa: BLE001 - 损坏 PDF 降级到纯文本
            meta.tried("pdf-load", f"failed ({e!r}), falling back to text")
            pages, reader, pdf_doc = None, None, None

    strategies = {
        cls.name: cls() for cls in registered_strategies() if cls.name in PRIORITY
    }
    sections = None
    for name in PRIORITY:
        strategy = strategies.get(name)
        if strategy is None:
            continue
        try:
            candidate = strategy.segment(pages=pages, text=text, reader=reader)
        except Exception as e:  # noqa: BLE001 - 单策略失败不炸整个 ingest
            meta.tried(name, f"error ({e!r})")
            continue
        if candidate is None:
            meta.tried(name, "not applicable")
            continue
        if not strategy.plausible(candidate):
            meta.tried(
                name,
                f"implausible ({len(candidate)} sections, "
                f"avg {sum(len(str(s['text'])) for s in candidate) // max(len(candidate), 1)} chars)",
            )
            continue
        sections = candidate
        meta.strategy = name
        meta.tried(name, "selected")
        break

    if sections is None:
        # 兜底也失败：把全部可用文本作为单一节，保证 job 能继续跑
        src = "\n\n".join(pages) if pages else (text or "")
        sections = [make_section(text=src, title="全文（未切分）")]
        meta.strategy = "single-blob"
        meta.tried("single-blob", "all strategies failed")

    for i, s in enumerate(sections):
        s["index"] = i
    _normalize_chapters(sections, meta)

    # 图表页渲染（BIS 图表是矢量图，唯一可靠路线是整页渲染）
    if pdf_doc is not None and source_id and image_root:
        try:
            from app.ingestion.images import attach_images

            meta.image_count = attach_images(sections, pdf_doc, source_id, Path(image_root))
        except Exception:
            logger.warning("image pipeline failed", exc_info=True)
            meta.image_count = 0
    return sections, meta


__all__ = ["IngestMeta", "ingest", "make_section", "registered_strategies"]
