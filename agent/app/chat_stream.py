"""SSE streaming for the chat endpoint.

Streams tokens plus tool-call lifecycle events from the deepagents graph so
the UI can show what the agent is doing while it answers:

    {"type": "token", "content": "..."}
    {"type": "tool_call", "id": "...", "name": "list_sources", "args": {...}}
    {"type": "tool_result", "id": "...", "name": "list_sources",
     "content": "...", "status": "success"}
    {"type": "final", "content": "..."} | {"type": "error", "message": "..."}

The event extraction is split into pure functions so tests can feed recorded
stream chunks without running a real LLM.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage

# Tool results can be huge (e.g. a full source list); cap what goes over SSE.
TOOL_RESULT_MAX_CHARS = 500


def text_of(content: Any) -> str:
    """Extract plain text from a message content (str or multimodal list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content) if content is not None else ""


def _truncate(text: str, limit: int = TOOL_RESULT_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... ({len(text) - limit} chars truncated)"


def token_event(chunk: Any) -> dict[str, Any] | None:
    """Token event from a messages-mode chunk; None when nothing to show."""
    if not isinstance(chunk, AIMessageChunk):
        return None
    text = text_of(chunk.content)
    if not text:
        return None
    return {"type": "token", "content": text}


def events_from_update(update: Any) -> list[dict[str, Any]]:
    """Tool-call lifecycle events from an updates-mode chunk.

    An updates chunk maps node name -> state delta; we scan every node's
    message list for AIMessages with tool_calls (tool started) and
    ToolMessages (tool finished).
    """
    events: list[dict[str, Any]] = []
    if not isinstance(update, dict):
        return events
    for delta in update.values():
        if not isinstance(delta, dict):
            continue
        for msg in delta.get("messages", []):
            events.extend(_events_from_message(msg))
    return events


def _events_from_message(msg: BaseMessage) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if isinstance(msg, AIMessage):
        for tc in msg.tool_calls or []:
            events.append(
                {
                    "type": "tool_call",
                    "id": tc.get("id") or "",
                    "name": tc.get("name") or "",
                    "args": tc.get("args") or {},
                }
            )
    elif isinstance(msg, ToolMessage):
        events.append(
            {
                "type": "tool_result",
                "id": msg.tool_call_id or "",
                "name": msg.name or "",
                "content": _truncate(text_of(msg.content)),
                "status": "error" if msg.status == "error" else "success",
            }
        )
    return events


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def stream_chat_turn(
    agent, lc_messages: list, callbacks: list | None = None
) -> AsyncIterator[str]:
    """Run the agent and yield SSE lines for tokens, tool calls and the final
    answer. The final event carries the complete text of the last AI message
    (source of truth for the client; individual tokens may be dropped by
    proxies mid-stream).

    custom mode events (written by tools via get_stream_writer, e.g. the
    extraction pipeline's nested task/submit_insight updates) are passed
    through as-is - the frontend renders them as tool-call cards.
    """
    last_ai_text = ""
    try:
        async for mode, chunk in agent.astream(
            {"messages": lc_messages},
            config={"callbacks": callbacks or []},
            stream_mode=["messages", "updates", "custom"],
        ):
            if mode == "messages":
                msg_chunk = chunk[0] if isinstance(chunk, tuple) else chunk
                event = token_event(msg_chunk)
                if event:
                    yield sse(event)
            elif mode == "custom":
                if isinstance(chunk, dict):
                    yield sse(chunk)
            elif mode == "updates":
                for delta in (
                    chunk.values() if isinstance(chunk, dict) else []
                ):
                    if not isinstance(delta, dict):
                        continue
                    for msg in delta.get("messages", []):
                        # Track the latest full AI text for the final event
                        if isinstance(msg, AIMessage):
                            text = text_of(msg.content)
                            if text:
                                last_ai_text = text
                for event in events_from_update(chunk):
                    yield sse(event)
    except Exception as e:  # noqa: BLE001 - stream boundary: report anything
        yield sse({"type": "error", "message": str(e)})
        return

    yield sse({"type": "final", "content": last_ai_text})
