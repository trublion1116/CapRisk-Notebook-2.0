"""Tests for chat agent tools (intent: extract core viewpoints)."""

from unittest.mock import AsyncMock, MagicMock, patch

from app.agents import CHAT_PREAMBLE, build_chat_agent


def _capture_tools(client: MagicMock | None = None):
    """Build the chat agent with create_deep_agent mocked; return its tools."""
    with (
        patch("app.agents.create_deep_agent") as create_agent,
        patch("app.agents.build_llm", return_value=MagicMock()),
        patch("app.agents.OpenNotebookClient", return_value=client),
    ):
        build_chat_agent()
    return create_agent.call_args.kwargs["tools"]


def test_chat_preamble_documents_extract_intent():
    assert "提取核心观点" in CHAT_PREAMBLE
    assert "start_core_viewpoint_extraction" in CHAT_PREAMBLE


def test_chat_agent_registers_intent_tools():
    tools = _capture_tools()
    names = {t.name for t in tools}
    assert {"list_sources", "start_core_viewpoint_extraction"} <= names


async def test_list_sources_tool_formats_rows():
    client = MagicMock()
    client.list_sources = AsyncMock(
        return_value=[
            {
                "id": "source:a",
                "title": "BIS 年度报告",
                "type": "PDF",
                "insights_count": 3,
            }
        ]
    )
    tools = _capture_tools(client)
    list_sources = next(t for t in tools if t.name == "list_sources")
    result = await list_sources.ainvoke({})

    client.list_sources.assert_awaited_once()
    assert "source:a" in result
    assert "BIS 年度报告" in result


async def test_start_extraction_tool_runs_synchronously_and_streams():
    """The extraction tool awaits the pipeline in-call (synchronous for the
    chat turn) and forwards pipeline events through the writer."""
    job = MagicMock(id="job123", status="done", sections=42, error=None)
    captured_events = []
    calls = []

    async def fake_run(job_arg, on_event=None):
        calls.append(job_arg)
        assert on_event is not None
        await on_event({"type": "tool_call", "id": "t1", "name": "task", "args": {}})

    with (
        patch("app.agents.create_job", return_value=job),
        patch("app.runner.run_extraction_job", fake_run),
        patch(
            "app.agents.get_stream_writer",
            side_effect=lambda: lambda ev: captured_events.append(ev),
        ),
    ):
        tools = _capture_tools()
        start = next(t for t in tools if t.name == "start_core_viewpoint_extraction")
        result = await start.ainvoke({"source_id": "source:a"})

    # Pipeline ran synchronously inside the tool call (awaited, not scheduled)
    assert calls == [job]
    # Pipeline events reached the chat graph's writer (-> custom stream)
    assert captured_events == [
        {"type": "tool_call", "id": "t1", "name": "task", "args": {}}
    ]
    assert "job123" in result
    assert "done" in result
