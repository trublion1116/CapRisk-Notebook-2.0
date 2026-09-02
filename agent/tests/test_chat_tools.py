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


async def test_start_extraction_tool_creates_background_job():
    job = MagicMock(id="job123")
    with (
        patch("app.agents.create_job", return_value=job) as create_job_mock,
        patch("app.runner.run_extraction_job", new_callable=AsyncMock) as run_job,
    ):
        tools = _capture_tools()
        start = next(t for t in tools if t.name == "start_core_viewpoint_extraction")
        result = await start.ainvoke({"source_id": "source:a"})

    create_job_mock.assert_called_once_with("source:a", "核心观点")
    assert "job123" in result
    # The tool schedules run_extraction_job as a background task: the
    # coroutine is created (called once with the job) but not awaited
    # inside the tool call itself.
    run_job.assert_called_once_with(job)
