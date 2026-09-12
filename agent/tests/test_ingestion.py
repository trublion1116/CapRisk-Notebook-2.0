"""Tests for the ingestion strategy chain (outline/regex/markdown/fallback)."""

from pathlib import Path

import pytest

from app.ingestion import ingest
from app.ingestion.heading_regex import HeadingRegexStrategy
from app.ingestion.markdown_header import MarkdownHeaderStrategy

REAL_PDF = Path("/tmp/opencode/ar2026e.pdf")  # 真实 BIS 报告（冒烟用，存在才跑）


def test_outline_strategy_segments_by_bookmarks(monkeypatch):
    # 上面构造复杂 PDF 太绕，直接 monkeypatch _flatten 简化书签部分，
    # 页文本由 pages 参数提供（策略只依赖这两个输入）。
    pages = [f"page {i} text " * 60 for i in range(10)]
    flat = [
        (0, "BIS Annual Economic Report 2026", 0),
        (1, "Contents", 0),
        (1, "I. Progress and peril", 2),
        (2, "Key takeaways", 2),
        (2, "Resilience tested", 3),
        (2, "Box A: Global supply chains", 4),
        (2, "The conflict and its peril", 5),
        (2, "Endnotes", 7),
        (1, "II. High public debt", 8),
        (2, "Introduction", 8),
    ]
    from app.ingestion import outline as outline_mod

    monkeypatch.setattr(outline_mod, "_flatten", lambda o, r: flat)

    from typing import ClassVar

    class _R:
        outline: ClassVar[list] = ["fake"]

    class _S(outline_mod.OutlineStrategy):
        pass

    sections = _S().segment(pages=pages, reader=_R())
    assert sections is not None
    by_title = {s["title"]: s for s in sections}
    # 根节点被跳过，章归属正确
    assert by_title["Key takeaways"]["chapter_title"] == "I. Progress and peril"
    assert by_title["Introduction"]["chapter_title"] == "II. High public debt"
    # Box 识别为专题框
    assert by_title["Box A: Global supply chains"]["kind"] == "box"
    # Endnotes 归为章后内容
    assert by_title["Endnotes"]["kind"] == "backmatter"
    # 页范围切分（书签页 → 下一书签页）
    assert by_title["Key takeaways"]["page_start"] == 2
    assert by_title["Key takeaways"]["page_end"] == 2


def test_heading_regex_finds_chapters_and_boxes():
    text = "\n".join(
        [
            "Introduction text here " * 20,
            "I. Progress and peril",
            "chapter one body " * 200,
            "Box A: Supply chains and vulnerability",
            "box body " * 100,
            "II. Markets under stress",
            "chapter two body " * 200,
        ]
    )
    sections = HeadingRegexStrategy().segment(text=text)
    assert sections is not None
    kinds = {(s["kind"], s["title"]) for s in sections}
    assert ("body", "I. Progress and peril") in kinds
    assert ("box", "Box A: Supply chains and vulnerability") in kinds
    assert ("body", "II. Markets under stress") in kinds
    # 章归属传播：Box A 属于第一章
    box = next(s for s in sections if s["kind"] == "box")
    assert box["chapter_title"] == "I. Progress and peril"
    # 边界按行切：I. 的正文不含 box 正文
    ch1 = next(s for s in sections if s["title"].startswith("I."))
    assert "box body" not in ch1["text"]
    assert "chapter one body" in ch1["text"]


def test_heading_regex_requires_multiple_anchors():
    text = "\n".join(["I. Only one chapter marker", "body " * 50])
    assert HeadingRegexStrategy().segment(text=text) is None


def test_markdown_header_splits_by_heading():
    text = "\n".join(
        [
            "# Chapter One",
            "body one " * 100,
            "## Section A",
            "body a " * 100,
            "## Section B",
            "body b " * 100,
            "# Chapter Two",
            "body two " * 100,
            "## Box B: Something",
            "box body " * 50,
        ]
    )
    sections = MarkdownHeaderStrategy().segment(text=text)
    assert sections is not None
    by_title = {s["title"]: s for s in sections}
    assert by_title["Section A"]["chapter_title"] == "Chapter One"
    assert by_title["Box B: Something"]["kind"] == "box"
    assert by_title["Box B: Something"]["chapter_title"] == "Chapter Two"


def test_markdown_header_needs_enough_headers():
    assert MarkdownHeaderStrategy().segment(text="# lone\nbody") is None


def test_ingest_falls_back_to_chars_on_plain_text():
    sections, meta = ingest(None, ("paragraph text " * 60 + "\n\n") * 30)
    assert meta.strategy == "char-fallback"
    assert len(sections) >= 3
    assert all(s["kind"] == "body" for s in sections)


@pytest.mark.skipif(not REAL_PDF.exists(), reason="real BIS PDF not available")
class TestRealBisPdf:
    def test_full_ingest_smoke(self, tmp_path):
        pdf_bytes = REAL_PDF.read_bytes()
        sections, meta = ingest(
            pdf_bytes, None, source_id="test-ar2026e", image_root=tmp_path
        )
        # 书签策略命中；章/box 结构存在；图表页有渲染
        assert meta.strategy == "outline"
        assert len(meta.chapters) >= 3
        assert meta.box_count >= 5
        assert meta.image_count >= 10
        # box 节的文本确实含 Box 内容（以 Box 标题或正文特征抽查）
        boxes = [s for s in sections if s["kind"] == "box"]
        assert any("Box" in (s["title"] or "") for s in boxes)
        # 图表 PNG 挂载到对应节（页范围相交）
        with_imgs = [s for s in sections if s.get("images")]
        assert with_imgs, "chart images should attach to sections"
        for s in with_imgs:
            assert len(s["images"]) == len(s.get("image_notes", [])) or not s.get(
                "image_notes"
            )
