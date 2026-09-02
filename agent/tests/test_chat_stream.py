"""Tests for chat SSE streaming (event extraction + stream_chat_turn)."""

import json
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.chat_stream import (
    events_from_update,
    sse,
    stream_chat_turn,
    text_of,
    token_event,
)


def _chunk(text: str) -> AIMessageChunk:
    return AIMessageChunk(content=text)


def test_text_of_handles_str_and_multimodal():
    assert text_of("hello") == "hello"
    assert text_of([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "ab"
    assert text_of(None) == ""


def test_token_event_skips_empty_and_non_chunks():
    assert token_event(_chunk("hi")) == {"type": "token", "content": "hi"}
    assert token_event(_chunk("")) is None
    assert token_event("not a chunk") is None


def test_events_from_update_extracts_tool_lifecycle():
    update = {
        "model": {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "call_1", "name": "list_sources", "args": {}}
                    ],
                ),
                ToolMessage(
                    content="source:a | BIS",
                    tool_call_id="call_1",
                    name="list_sources",
                ),
            ]
        }
    }
    events = events_from_update(update)

    assert events[0] == {
        "type": "tool_call",
        "id": "call_1",
        "name": "list_sources",
        "args": {},
    }
    assert events[1]["type"] == "tool_result"
    assert events[1]["status"] == "success"
    assert "BIS" in events[1]["content"]


def test_tool_result_truncated():
    ToolMessage  # noqa: B018 - reference for readability
    long = "x" * 2000
    events = events_from_update(
        {"n": {"messages": [ToolMessage(content=long, tool_call_id="c", name="t")]}}
    )
    assert len(events[0]["content"]) < 600
    assert "truncated" in events[0]["content"]


def test_sse_format():
    line = sse({"type": "token", "content": "你"})
    assert line.startswith("data: ")
    assert json.loads(line[6:])["content"] == "你"
    assert line.endswith("\n\n")


class _FakeAgent:
    """Yields a scripted (mode, chunk) stream like LangGraph astream."""

    def __init__(self, chunks: list[tuple[str, Any]], error: Exception | None = None):
        self._chunks = chunks
        self._error = error

    async def astream(self, _input, config=None, stream_mode=None):
        for mode, chunk in self._chunks:
            yield mode, chunk
        if self._error:
            raise self._error


async def test_stream_chat_turn_full_lifecycle():
    chunks = [
        ("messages", (_chunk("让我"), {})),
        ("messages", (_chunk("查一下来源。"), {})),
        (
            "updates",
            {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {"id": "c1", "name": "list_sources", "args": {}}
                            ],
                        )
                    ]
                }
            },
        ),
        (
            "updates",
            {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content="source:a | BIS 报告",
                            tool_call_id="c1",
                            name="list_sources",
                        ),
                        AIMessage(content="最终回答：BIS 报告共 1 个来源。"),
                    ]
                }
            },
        ),
    ]
    lines = [line async for line in stream_chat_turn(_FakeAgent(chunks), [])]

    payloads = [json.loads(line[6:]) for line in lines]
    types = [p["type"] for p in payloads]

    assert types[:2] == ["token", "token"]
    assert "tool_call" in types and "tool_result" in types
    assert types[-1] == "final"
    final = payloads[-1]
    assert final["content"] == "最终回答：BIS 报告共 1 个来源。"


async def test_stream_chat_turn_passes_custom_events_through():
    """custom-mode events (nested pipeline via get_stream_writer) are
    forwarded as-is so the frontend renders them as tool cards."""
    chunks = [
        ("custom", {"type": "tool_call", "id": "t1", "name": "task", "args": {"batch": "0-14"}}),
        ("custom", {"type": "tool_result", "id": "t1", "name": "task", "content": "...", "status": "success"}),
        (
            "updates",
            {"model": {"messages": [AIMessage(content="提取完成")]}},
        ),
    ]
    lines = [line async for line in stream_chat_turn(_FakeAgent(chunks), [])]

    payloads = [json.loads(line[6:]) for line in lines]
    assert payloads[0]["name"] == "task"
    assert payloads[1]["type"] == "tool_result"
    assert payloads[-1] == {"type": "final", "content": "提取完成"}


async def test_stream_chat_turn_error_yields_error_event():
    agent = _FakeAgent([], error=RuntimeError("LLM down"))
    lines = [line async for line in stream_chat_turn(agent, [])]

    payload = json.loads(lines[-1][6:])
    assert payload == {"type": "error", "message": "LLM down"}
