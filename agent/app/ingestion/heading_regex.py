"""正则启发式切片——无书签 PDF 的次选路线。

只切**章级**结构（罗马数字章标题 ``I. ...`` / ``Chapter N``）和 Box 标题
（``Box A: ...`` 独立行）。小节级标题在纯文本层无字号信息，误判率高，
刻意不切——章内内容交给 worker 全覆盖阅读，结构粒度到章已够用
（章节归属、box 剥离都成立）。
"""

import re

from app.ingestion.base import SegmentationStrategy, make_section

CHAPTER_LINE = re.compile(r"^([IVXL]+\.\s+[^\n]{3,90}|Chapter\s+\d+[^\n]{0,80})$")
BOX_LINE = re.compile(r"^(Box\s+[A-Z]\s*:?[^\n]{3,120})$")
BACKMATTER_LINE = re.compile(r"^(Endnotes|References)[\s:]*$", re.IGNORECASE)
# 行内噪声过滤：太短的行（如孤立的 "III."）不足以判定为标题
MIN_TITLE_LEN = 8


class HeadingRegexStrategy(SegmentationStrategy):
    name = "heading-regex"

    def segment(self, pages=None, text=None, reader=None):
        # pages 优先（带页码归属）；纯文本也接受（无页码，page_* 为 None）
        if pages:
            lines: list[tuple[str, int | None]] = []
            for pno, ptext in enumerate(pages):
                for line in ptext.splitlines():
                    lines.append((line, pno))
        elif text:
            lines = [(ln, None) for ln in text.splitlines()]
        else:
            return None

        # 锚点 = (行号, kind, 标题, 页码)。用行号切边界，文本不重复挂载。
        anchors: list[tuple[int, str, str, int | None]] = []
        for idx, (line, pno) in enumerate(lines):
            clean = line.strip()
            if len(clean) < MIN_TITLE_LEN:
                continue
            if BOX_LINE.match(clean):
                anchors.append((idx, "box", clean.rstrip(":"), pno))
            elif CHAPTER_LINE.match(clean):
                anchors.append((idx, "chapter", clean, pno))
            elif BACKMATTER_LINE.match(clean):
                anchors.append((idx, "backmatter", clean, pno))
        # 至少识别出 2 个章/box 锚点才可信，否则交由兜底策略
        if sum(1 for _, k, _, _ in anchors if k in ("chapter", "box")) < 2:
            return None

        sections: list[dict] = []
        current_chapter = "前言"
        for ai, (line_idx, kind, title, pno) in enumerate(anchors):
            end_idx = anchors[ai + 1][0] if ai + 1 < len(anchors) else len(lines)
            body = "\n".join(ln for ln, _ in lines[line_idx + 1 : end_idx]).strip()
            if kind == "chapter":
                current_chapter = title
            sections.append(
                make_section(
                    text=body,
                    title=title,
                    chapter_title=current_chapter,
                    level=1,
                    kind="body" if kind == "chapter" else kind,
                    page_start=pno,
                    page_end=pno,
                )
            )
        return sections
