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
    prompt = build_orchestrator_prompt(
        101, 15, subagent_catalog(), outline="- I. Foo：第 0-50 节；专题框：Box A: X"
    )

    assert "- viewpoint-extraction:" in prompt
    assert "101" in prompt and "15" in prompt
    assert "Box A: X" in prompt
    assert "submit_report" in prompt  # 报告模式：单次提交整篇


def test_build_outline_groups_chapters_with_boxes():
    from app.agents import build_outline

    sections = [
        {"index": 0, "chapter_title": "I. Foo", "kind": "body"},
        {"index": 1, "chapter_title": "I. Foo", "kind": "box", "title": "Box A: Supply"},
        {"index": 2, "chapter_title": "I. Foo", "kind": "backmatter"},
        {"index": 3, "chapter_title": "II. Bar", "kind": "body"},
    ]
    outline = build_outline(sections)

    assert "I. Foo：第 0-2 节" in outline
    assert "专题框：Box A: Supply" in outline
    assert "II. Bar：第 3-3 节" in outline
    assert "1 节章后内容" in outline


@pytest.fixture
def fake_llm_config(monkeypatch):
    """假 LLM 配置：build_llm 能构造真实 ChatOpenAI（不发起网络调用），
    deepagents 的 provider profile 需要真实模型对象做 token 比较。"""
    import app.config as config_mod

    monkeypatch.setattr(config_mod, "LLM_BASE_URL", "http://localhost:1/v1")
    monkeypatch.setattr(config_mod, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(config_mod, "LLM_MODEL", "test-model")


def test_orchestrator_tools_are_report_mode(fake_llm_config):
    from app.agents import build_list_charts_tool

    sections = [
        {
            "index": 0,
            "text": "t",
            "chapter_title": "I. Foo",
            "kind": "body",
            "page_start": 3,
            "page_end": 3,
            "images": ["/tmp/x/page_004.png"],
            "image_notes": ["[Graph 1] 增长韧性图"],
        }
    ]
    tool = build_list_charts_tool(sections, "source:t")
    assert tool.name == "list_charts"
    assert "图表" in tool.description


def test_list_charts_renders_markdown_refs(monkeypatch):
    import app.config as config_mod
    from app.agents import build_list_charts_tool

    monkeypatch.setattr(config_mod, "AGENT_PUBLIC_URL", "")  # 默认：相对路径走代理
    sections = [
        {
            "index": 0,
            "text": "t",
            "chapter_title": "I. Foo",
            "kind": "body",
            "page_start": 3,
            "page_end": 3,
            "images": ["/data/images/src1/page_004.png"],
            "image_notes": ["[Graph 1]\n增长韧性"],
        }
    ]
    list_charts = build_list_charts_tool(sections, "src1")

    out = list_charts.invoke({})
    assert "![(Graph 1) 增长韧性](/agent-images/src1/page_004.png)" in out
    assert "p3" in out and "I. Foo" in out


def test_list_charts_absolute_url_when_configured(monkeypatch):
    import app.config as config_mod
    from app.agents import build_list_charts_tool

    monkeypatch.setattr(config_mod, "AGENT_PUBLIC_URL", "http://192.168.1.5:5060")
    tool = build_list_charts_tool(
        [{"index": 0, "text": "t", "images": ["/x/y.png"], "image_notes": ["n"]}],
        "s",
    )
    assert "http://192.168.1.5:5060/images/s/y.png" in tool.invoke({})


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
