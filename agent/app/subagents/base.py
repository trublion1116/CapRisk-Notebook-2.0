"""SubAgent 基类与声明式注册机制。

新增一个能力子代理只需在本包下新建一个模块，定义::

    class XxxSubAgent(BaseSubAgent):
        name = "xxx-agent"
        description = "一句话职责，自动拼进编排 prompt"
        SYSTEM_PROMPT = "...完整工作协议（多行字符串）..."

        def build_tools(self, context):
            ...返回绑定 context 的工具列表...

模块被包内自动发现（见 ``__init__.py``），类在定义时即注册——
编排 agent 的子代理列表与 prompt 目录随之自动更新，
不需要修改注册表、编排 prompt 或任何其他代码（开闭原则）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool

from app.on_client import OpenNotebookClient


@dataclass(frozen=True)
class SubAgentContext:
    """一次提取任务中所有子代理共享的运行时上下文。

    新增子代理需要的上下文字段时在这里扩展，所有子代理都能取到，
    不用改注册表和编排层的签名。
    """

    client: OpenNotebookClient
    sections: list[dict[str, Any]]
    source_id: str
    insight_type: str


_REGISTRY: list[type["BaseSubAgent"]] = []


class BaseSubAgent(ABC):
    """能力子代理基类：元信息与功能内聚于子类。

    类属性（叶子子类必须提供）：
        name:         子代理唯一名（kebab-case），用于 task 派发与 Langfuse 展示
        description:  一句话职责描述——编排 prompt 的子代理目录自动拼接它
        SYSTEM_PROMPT: 子代理自己的完整工作协议

    注册规则：只有**直接声明了非空 ``name``** 的子类才会被注册，
    因此可以定义不带 name 的中间抽象基类而不被收录。
    """

    name: str = ""
    description: str = ""
    SYSTEM_PROMPT: str = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared_name = cls.__dict__.get("name", "")
        if not declared_name:
            return
        if any(declared_name == existing.name for existing in _REGISTRY):
            raise ValueError(
                f"Duplicate subagent name: {declared_name!r} already registered"
            )
        _REGISTRY.append(cls)

    @abstractmethod
    def build_tools(self, context: SubAgentContext) -> list[BaseTool]:
        """构造绑定到本次任务上下文的工具。"""

    def to_spec(self, context: SubAgentContext) -> dict[str, Any]:
        """渲染为 deepagents SubAgent 声明式规格。"""
        if not self.name or not self.description or not self.SYSTEM_PROMPT:
            raise ValueError(
                f"{type(self).__name__} must define name, description and SYSTEM_PROMPT"
            )
        return {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.SYSTEM_PROMPT,
            "tools": self.build_tools(context),
        }


def registered_subagent_classes() -> list[type[BaseSubAgent]]:
    """全部已注册的子代理类（按定义顺序）。"""
    return list(_REGISTRY)


def registered_subagents(context: SubAgentContext) -> list[dict[str, Any]]:
    """实例化全部子代理并渲染为 deepagents 规格。"""
    return [cls().to_spec(context) for cls in _REGISTRY]


def subagent_catalog() -> list[str]:
    """子代理目录（编排 prompt 用）：一行一个 ``- name: description``。"""
    return [f"- {cls.name}: {cls.description}" for cls in _REGISTRY]
