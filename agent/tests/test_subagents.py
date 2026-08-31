"""Tests for the declarative subagent registry and orchestrator wiring."""

from unittest.mock import MagicMock

import pytest

from app.agents import build_orchestrator_prompt
from app.subagents import (
    SubAgentContext,
    registered_subagent_classes,
    registered_subagents,
    subagent_catalog,
)
from app.subagents.base import BaseSubAgent
from app.subagents.viewpoint_extraction import ViewpointExtractionSubAgent

SECTIONS = [
    {"index": 0, "text": "第一章 全球经济展望。"},
    {"index": 1, "text": "第二章 金融市场。"},
]


def _context():
    return SubAgentContext(
        client=MagicMock(),
        sections=SECTIONS,
        source_id="source:x",
        insight_type="核心观点",
    )


def test_viewpoint_extraction_auto_registered():
    names = [cls.name for cls in registered_subagent_classes()]

    assert "viewpoint-extraction" in names


def test_registry_renders_deepagents_spec():
    specs = registered_subagents(_context())

    spec = next(s for s in specs if s["name"] == "viewpoint-extraction")
    assert spec["description"]
    assert "全覆盖" in spec["system_prompt"]
    tool_names = {t.name for t in spec["tools"]}
    assert tool_names == {"list_sections", "read_section"}


def test_extraction_subagent_has_no_submission_tool():
    spec = ViewpointExtractionSubAgent().to_spec(_context())

    assert "submit_insight" not in {t.name for t in spec["tools"]}


def test_catalog_lists_name_and_description():
    catalog = subagent_catalog()

    entry = next(line for line in catalog if "viewpoint-extraction" in line)
    assert entry.startswith("- viewpoint-extraction: ")
    assert "候选清单" in entry


def test_orchestrator_prompt_assembles_catalog_dynamically():
    prompt = build_orchestrator_prompt(101, 15, subagent_catalog())

    assert "- viewpoint-extraction:" in prompt
    assert "101" in prompt and "15" in prompt


def test_duplicate_name_rejected():
    with pytest.raises(ValueError, match="Duplicate subagent name"):

        class Imposter(BaseSubAgent):
            name = "viewpoint-extraction"
            description = "重名子代理"
            SYSTEM_PROMPT = "x"

            def build_tools(self, context):
                return []


def test_abstract_intermediate_base_not_registered():
    class ReadingBase(BaseSubAgent):  # 无 name：中间抽象基类，不应注册
        def build_tools(self, context):
            return []

    before = {cls.name for cls in registered_subagent_classes()}

    class ConcreteReader(ReadingBase):
        name = "test-concrete-reader"
        description = "测试"
        SYSTEM_PROMPT = "x"

        def build_tools(self, context):
            return []

    assert "test-concrete-reader" in {cls.name for cls in registered_subagent_classes()}
    assert ReadingBase not in registered_subagent_classes()
    assert before  # sanity: registry non-empty
    # 清理：测试注册的类不影响其他断言（registered_subagent_classes 返回副本）
    from app.subagents.base import _REGISTRY

    _REGISTRY.remove(ConcreteReader)


def test_read_section_tool_resolves_text():
    spec = ViewpointExtractionSubAgent().to_spec(_context())
    read_section = next(t for t in spec["tools"] if t.name == "read_section")

    assert "金融市场" in read_section.invoke({"index": 1})
    assert "not found" in read_section.invoke({"index": 9})
