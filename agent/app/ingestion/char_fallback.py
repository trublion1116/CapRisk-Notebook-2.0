"""字符兜底切片——策略链末位，行为与旧版 split_sections 一致。

无任何结构信号时的保底：段落保持完整的字符切分，只保证全覆盖，
无章节归属。迁移自 app/extraction.py 的 split_sections（该函数保留
以兼容旧调用方）。
"""

from app.extraction import split_sections
from app.ingestion.base import SegmentationStrategy, make_section


class CharFallbackStrategy(SegmentationStrategy):
    name = "char-fallback"

    def segment(self, pages=None, text=None, reader=None):
        src = "\n\n".join(pages) if pages else text
        if not src or not src.strip():
            return None
        # 复用 split_sections 的切分逻辑，但补齐结构字段（kind/page 等），
        # 保证下游消费者拿到的 Section 形状在所有策略下一致。
        return [
            make_section(text=str(s["text"]), title=f"第 {s['index'] + 1} 段")
            for s in split_sections(src)
        ]
