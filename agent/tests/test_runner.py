"""Tests for the extraction job source-material path selection."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.jobs import ExtractionJob
from app.runner import MIN_STORED_TEXT_CHARS, fetch_source_material


def _client(full_text=None, downloaded=b"%PDF-1.7 fake"):
    client = MagicMock()
    client.get_source = AsyncMock(
        return_value={"id": "source:x", "title": "t", "full_text": full_text}
    )
    client.download_source = AsyncMock(return_value=downloaded)
    return client


def _job():
    return ExtractionJob(id="j1", source_id="source:x", insight_type="核心观点")


def test_prefers_pdf_with_text_as_fallback_input():
    # PDF 优先（书签/图表结构只有 PDF 有）；on-parsed 文本保留给策略链兜底
    client = _client(full_text="x" * MIN_STORED_TEXT_CHARS)

    pdf, text, origin = asyncio.run(fetch_source_material(client, _job()))

    assert origin == "pdf+on-parsed"
    assert pdf == b"%PDF-1.7 fake"
    assert len(text) == MIN_STORED_TEXT_CHARS
    client.download_source.assert_awaited_once_with("source:x")


def test_pdf_only_when_text_missing():
    client = _client(full_text=None)

    pdf, text, origin = asyncio.run(fetch_source_material(client, _job()))

    assert origin == "pdf"
    assert pdf is not None
    assert text is None


def test_uses_stored_text_when_download_fails():
    client = _client(full_text="x" * MIN_STORED_TEXT_CHARS)
    client.download_source = AsyncMock(side_effect=RuntimeError("boom"))

    pdf, text, origin = asyncio.run(fetch_source_material(client, _job()))

    assert origin == "on-parsed"
    assert pdf is None
    assert len(text) == MIN_STORED_TEXT_CHARS


def test_non_pdf_download_is_ignored():
    # 上传源是纯文本/网页时 download 不返回 PDF——退回 on-parsed 文本
    client = _client(full_text="x" * MIN_STORED_TEXT_CHARS, downloaded=b"plain text")

    pdf, _, origin = asyncio.run(fetch_source_material(client, _job()))

    assert origin == "on-parsed"
    assert pdf is None


def test_too_short_text_counts_as_missing():
    client = _client(full_text="too short")
    client.download_source = AsyncMock(side_effect=RuntimeError("boom"))

    pdf, text, origin = asyncio.run(fetch_source_material(client, _job()))

    assert origin == "empty"
    assert pdf is None
    assert text == ""
