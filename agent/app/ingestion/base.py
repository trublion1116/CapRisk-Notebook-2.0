"""数据面 ingestion：报告 → 结构化 Section 列表。

切片策略链（按序尝试，自动降级）：
    outline（PDF 书签，含 Box/章结构）→ markdown_header（docling 产物的
    # 标题层级）→ heading_regex（文本层正则启发）→ char_fallback（字符切分）。

新策略 = 在本包新建模块、定义 ``SegmentationStrategy`` 子类并声明非空
``name``，包内自动发现（见 ``__init__.py``），无需改注册代码——与
``app/subagents`` 同一套开闭模式。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Section 统一用 dict（而非 dataclass）：现有 worker 工具按 s["index"] /
# s["text"] 取值（见 viewpoint_extraction.py），dict 保持零改动兼容，
# 新增键对旧代码不可见。
SECTION_KEYS = (
    "index",
    "text",
    "title",
    "chapter_title",
    "level",
    "kind",
    "page_start",
    "page_end",
    "images",
    "image_notes",
)

# kind 语义：
#   body       正文章节（默认）
#   box        BIS 专题框（Box A/B/C...），信息密度高，独立成节重点提取
#   frontmatter 目录/惯例说明等章前内容
#   backmatter Endnotes/References 等章后内容，提取价值低


def make_section(
    *,
    text: str,
    title: str = "",
    chapter_title: str = "",
    level: int = 0,
    kind: str = "body",
    page_start: int | None = None,
    page_end: int | None = None,
) -> dict[str, Any]:
    """构造一个 Section。images/image_notes 由图片管线后续填充。"""
    return {
        "index": -1,  # 由 ingest() 统一编号
        "text": text,
        "title": title,
        "chapter_title": chapter_title,
        "level": level,
        "kind": kind,
        "page_start": page_start,
        "page_end": page_end,
        "images": [],
        "image_notes": [],
    }


@dataclass
class IngestMeta:
    """ingest 过程的元信息，供 job log 与事件透传。"""

    strategy: str = ""
    strategy_chain: list[str] = field(default_factory=list)  # 尝试记录（含放弃原因）
    chapters: list[str] = field(default_factory=list)
    box_count: int = 0
    backmatter_count: int = 0
    image_count: int = 0

    def tried(self, strategy: str, outcome: str) -> None:
        self.strategy_chain.append(f"{strategy}: {outcome}")


class SegmentationStrategy(ABC):
    """切片策略基类。

    子类声明非空 ``name`` 即自动注册。``segment`` 返回 None 表示
    本策略不适用（输入不满足前提或产出不合理），交由下一策略。
    """

    name: str = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 与 subagents 相同的声明式注册：只有直接声明非空 name 的子类
        # 才入注册表（允许定义中间基类而不被收录）。
        declared = cls.__dict__.get("name", "")
        if not declared:
            return
        if any(declared == existing.name for existing in _REGISTRY):
            raise ValueError(f"Duplicate ingestion strategy: {declared!r}")
        _REGISTRY.append(cls)

    @abstractmethod
    def segment(
        self,
        pages: list[str] | None = None,
        text: str | None = None,
        reader: Any = None,
    ) -> list[dict[str, Any]] | None:
        """按页文本（PDF 已逐页提取，含 [第N页] 标记）或纯文本切分。

        Args:
            pages: PDF 逐页文本列表（0-based 下标 = 页码），无 PDF 时为 None。
            text: ON 解析的 full_text（可能是 docling markdown），无则为 None。
            reader: pypdf PdfReader（书签等结构信息），无 PDF 时为 None。
        """

    def plausible(self, sections: list[dict[str, Any]]) -> bool:
        """产出合理性门：节数过少/过碎或平均过短视为不可信，触发降级。"""
        if not (3 <= len(sections) <= 300):
            return False
        avg = sum(len(str(s["text"])) for s in sections) / len(sections)
        return avg >= 300


_REGISTRY: list[type[SegmentationStrategy]] = []


def registered_strategies() -> list[type[SegmentationStrategy]]:
    return list(_REGISTRY)
