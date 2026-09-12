"""书签（outline）切片策略——BIS 报告的首选路线。

BIS 年度报告的 PDF 书签完整保留章/节/小节层级，且 **Box A/B/C 专题框
本身就是书签条目**（含起始页码），因此结构信息零成本可得，无需猜测
文本边界。局限：书签只给页级起点，同一页上多个书签无法精确切分文本，
此时该页文本会重复挂给相邻节——覆盖度无损（终审去重），可接受。
"""

import logging
import re

from pypdf import PdfReader

from app.ingestion.base import SegmentationStrategy, make_section

logger = logging.getLogger(__name__)

# 章标题："I. Progress and peril" / "III. Anchoring trust in money..."
ROMAN_CHAPTER = re.compile(r"^[IVXL]+\.\s+\S")
# Box 专题框标题（书签或文本行）："Box A: ..." / "Box B ..."
BOX_TITLE = re.compile(r"^Box\s+[A-Z]\b")
# 章后低价值内容
BACKMATTER = re.compile(r"^(Endnotes|References|Additional notes)", re.IGNORECASE)
# 整书根节点（书签第 0 层，通常与报告同名），跳过
ROOT_HINTS = ("bis annual economic report", "annual economic report")


def _flatten(outline: object, reader: PdfReader) -> list[tuple[int, str, int]]:
    """书签树 → 扁平 (level, title, page0) 序列，保持阅读顺序。"""
    out: list[tuple[int, str, int]] = []

    def walk(items: object, depth: int) -> None:
        for it in items:  # type: ignore[union-attr]
            if isinstance(it, list):
                walk(it, depth + 1)
            else:
                try:
                    page = reader.get_destination_page_number(it)
                except Exception:  # 损坏书签条目跳过即可
                    logger.debug("bad outline entry skipped", exc_info=True)
                    continue
                title = " ".join(str(it.title).split())
                out.append((depth, title, page))

    walk(outline, 0)
    return out


def _classify(title: str) -> tuple[str, int]:
    """书签标题 → (kind, level)。level 用书签深度，识别不了的归 body。"""
    if BOX_TITLE.match(title):
        return "box", 3
    if BACKMATTER.match(title):
        return "backmatter", 2
    return "body", 1


class OutlineStrategy(SegmentationStrategy):
    name = "outline"

    def segment(self, pages=None, text=None, reader=None):
        if not pages or reader is None:
            return None  # 无 PDF（页文本+书签 reader 缺一不可）
        flat = _flatten(reader.outline, reader)
        if len(flat) < 3:
            return None

        sections = []
        current_chapter = "前言"  # 第一个章书签之前的内容（目录/惯例等）
        root_dropped = False

        for i, (depth, title, page) in enumerate(flat):
            # 跳过整书根节点（与报告同名、覆盖全书的第 0 层书签）
            if depth == 0 and not root_dropped:
                root_dropped = True
                continue

            if ROMAN_CHAPTER.match(title):
                current_chapter = title
            kind, level = _classify(title)
            if kind == "body" and depth <= 1 and not ROMAN_CHAPTER.match(title):
                kind = "frontmatter" if current_chapter == "前言" else kind

            # 文本边界：本书签页 → 下一书签页（同页相邻则退化为单页文本，
            # 重复挂载，见模块 docstring）
            next_page = (
                flat[i + 1][2] if i + 1 < len(flat) else min(page + 1, len(pages))
            )
            end = max(next_page, page + 1)  # 至少覆盖本页
            body = "\n\n".join(f"[第{p + 1}页]\n{pages[p]}" for p in range(page, end))

            sections.append(
                make_section(
                    text=body,
                    title=title,
                    chapter_title=current_chapter,
                    level=level,
                    kind=kind,
                    page_start=page,
                    page_end=end - 1,
                )
            )
        return sections
