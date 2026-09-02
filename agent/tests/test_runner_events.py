"""Tests for run_extraction_job's on_event streaming mode."""

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from app.jobs import create_job
from app.runner import run_extraction_job


class _FakeOrchestrator:
    """Yields task-dispatch + submit lifecycle updates like deepagents."""

    def __init__(self):
        self.invoked = False

    async def ainvoke(self, _input, config=None):
        self.invoked = True

    async def astream(self, _input, config=None, stream_mode=None):
        assert stream_mode == ["updates"]
        yield "updates", {
            "model": {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "c1",
                                "name": "task",
                                "args": {"description": "阅读第 0-14 节"},
                            }
                        ],
                    )
                ]
            }
        }
        yield "updates", {
            "tools": {
                "messages": [
                    ToolMessage(
                        content="候选 3 条: ...",
                        tool_call_id="c1",
                        name="task",
                    ),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "c2",
                                "name": "submit_insight",
                                "args": {"quote": "circular financing"},
                            }
                        ],
                    ),
                ]
            }
        }


async def test_on_event_mode_streams_lifecycle(monkeypatch):
    job = create_job("source:x", "核心观点")
    fake = _FakeOrchestrator()

    async def fake_fetch(client, job):
        return "text " * 300, "on-parsed"

    with (
        patch("app.runner.OpenNotebookClient", MagicMock()),
        patch("app.runner.fetch_source_text", fake_fetch),
        patch("app.runner.split_sections", lambda text: ["s1"] * 10),
        patch("app.runner.build_orchestrator_agent", return_value=fake),
        patch("app.runner.traced", MagicMock()),
        patch("app.runner.child_span", MagicMock()),
    ):
        events = []

        async def on_event(event):
            events.append(event)

        await run_extraction_job(job, on_event=on_event)

    assert job.status == "done"
    assert fake.invoked is False  # astream path used, not ainvoke
    names = [(e["type"], e["name"]) for e in events]
    assert ("tool_call", "task") in names
    assert ("tool_result", "task") in names
    assert ("tool_call", "submit_insight") in names
    quotes = [
        e["args"].get("quote") for e in events if e["name"] == "submit_insight"
    ]
    assert "circular financing" in quotes


async def test_without_on_event_uses_ainvoke(monkeypatch):
    job = create_job("source:x", "核心观点")
    fake = _FakeOrchestrator()

    async def fake_fetch(client, job):
        return "text " * 300, "on-parsed"

    with (
        patch("app.runner.OpenNotebookClient", MagicMock()),
        patch("app.runner.fetch_source_text", fake_fetch),
        patch("app.runner.split_sections", lambda text: ["s1"] * 10),
        patch("app.runner.build_orchestrator_agent", return_value=fake),
        patch("app.runner.traced", MagicMock()),
        patch("app.runner.child_span", MagicMock()),
    ):
        await run_extraction_job(job)

    assert job.status == "done"
    assert fake.invoked is True
