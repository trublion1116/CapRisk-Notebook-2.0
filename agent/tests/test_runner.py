"""Tests for the extraction job source-text path selection."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.jobs import ExtractionJob
from app.runner import MIN_STORED_TEXT_CHARS, fetch_source_text


def _client(full_text=None, downloaded=b"plain text source content"):
    client = MagicMock()
    client.get_source = AsyncMock(
        return_value={"id": "source:x", "title": "t", "full_text": full_text}
    )
    client.download_source = AsyncMock(return_value=downloaded)
    return client


def _job():
    return ExtractionJob(id="j1", source_id="source:x", insight_type="核心观点")


def test_uses_stored_full_text_when_available():
    client = _client(full_text="x" * MIN_STORED_TEXT_CHARS)

    text, origin = asyncio.run(fetch_source_text(client, _job()))

    assert origin == "on-parsed"
    assert len(text) == MIN_STORED_TEXT_CHARS
    client.download_source.assert_not_awaited()


def test_falls_back_to_pdf_when_text_missing():
    client = _client(full_text=None)

    text, origin = asyncio.run(fetch_source_text(client, _job()))

    assert origin == "pdf-fallback"
    client.download_source.assert_awaited_once_with("source:x")
    assert text == "plain text source content"


def test_falls_back_to_pdf_when_text_too_short():
    client = _client(full_text="too short")

    _, origin = asyncio.run(fetch_source_text(client, _job()))

    assert origin == "pdf-fallback"
    client.download_source.assert_awaited_once()
