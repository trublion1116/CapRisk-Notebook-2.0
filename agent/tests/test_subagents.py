"""Tests for the subagent registry and orchestrator wiring."""

from unittest.mock import MagicMock

from app.subagents import registered_subagents
from app.subagents.viewpoint_extraction import build as build_viewpoint_extraction

SECTIONS = [
    {"index": 0, "text": "第一章 全球经济展望。"},
    {"index": 1, "text": "第二章 金融市场。"},
]


def _client():
    return MagicMock()


def test_registry_returns_viewpoint_extraction_spec():
    specs = registered_subagents(_client(), SECTIONS, "source:x", "核心观点")

    assert len(specs) >= 1
    spec = specs[0]
    assert spec["name"] == "viewpoint-extraction"
    # declarative deepagents SubAgent spec fields
    assert spec["description"]
    assert "全覆盖" in spec["system_prompt"]
    tool_names = {t.name for t in spec["tools"]}
    assert tool_names == {"list_sections", "read_section"}


def test_extraction_subagent_has_no_submission_tool():
    spec = build_viewpoint_extraction(_client(), SECTIONS)

    tool_names = {t.name for t in spec["tools"]}
    assert "submit_insight" not in tool_names


def test_read_section_tool_resolves_text():
    spec = build_viewpoint_extraction(_client(), SECTIONS)
    read_section = next(t for t in spec["tools"] if t.name == "read_section")

    assert "金融市场" in read_section.invoke({"index": 1})
    assert "not found" in read_section.invoke({"index": 9})
