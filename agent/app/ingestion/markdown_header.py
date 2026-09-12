"""markdown 标题切片——docling 引擎产物（ON 摄取侧）的适配路线。

ON 开启 docling 后 full_text 是带 ``#/##/###`` 层级的 markdown，标题
结构现成。此策略在 PDF 路径（书签/正则）都不可用时启用，输入为
on-parsed full_text。
"""

import re

from app.ingestion.base import SegmentationStrategy, make_section

HEADER_LINE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
# 至少这么多标题行才认定是 docling markdown（普通文本碰巧含 # 的概率低）
MIN_HEADERS = 5


class MarkdownHeaderStrategy(SegmentationStrategy):
    name = "markdown-header"

    def segment(self, pages=None, text=None, reader=None):
        if pages or not text:
            return None  # 有 PDF 时走 outline/regex，此策略只吃纯文本
        headers = list(HEADER_LINE.finditer(text))
        if len(headers) < MIN_HEADERS:
            return None

        sections: list[dict] = []
        for i, m in enumerate(headers):
            level = len(m.group(1))
            title = m.group(2).strip()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            body = text[m.end() : end].strip()
            # # 级视为章，其余视为节；docling 无页码信息
            sections.append(
                make_section(
                    text=body,
                    title=title,
                    chapter_title=title if level == 1 else "",
                    level=level,
                    kind="box" if title.lower().startswith("box ") else "body",
                )
            )
        # 章标题向下传播（docling 输出顺序即阅读顺序）
        current = ""
        for s in sections:
            if s["level"] == 1:
                current = s["title"]
            else:
                s["chapter_title"] = current or s["title"]
        return sections
