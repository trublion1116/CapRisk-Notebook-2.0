"""图表解读子代理（chart-reader）。

归一化声明：图表解读是提取流水线的独立执行单元（与其余三阶段——
数据面抠图 / viewpoint-extraction / 编排合成——并列）。**实际调度由
摄取期代码循环完成**（确定性：缓存命中/失败降级/并发控制依赖代码
语义，LLM 编排不可控），因此本类只承载工作协议（SYSTEM_PROMPT）与
身份标识，供日志/span/事件统一归因；``build_tools`` 返回空——
编排 agent 请勿 task 派发（description 已注明），描述能力由
``app/vision.py`` 的 provider 直调实现。
"""

from langchain_core.tools import BaseTool

from app.subagents.base import BaseSubAgent, SubAgentContext
from app.vision import CHART_PROMPT, describe_chart  # noqa: F401 - provider 层

CHART_READER_NAME = "chart-reader"


class ChartReaderSubAgent(BaseSubAgent):
    name = CHART_READER_NAME
    description = (
        "图表解读：对裁剪出的图表区域 PNG 生成结构化描述（图号/坐标/图例/"
        "关键数值/趋势）。摄取期已由代码逐图调度完成，编排无需 task 派发。"
    )

    # 工作协议单一事实来源在 vision.py（provider 层）——本类引用它，
    # 保持依赖单向：chart_reader → vision（反向会循环导入）
    SYSTEM_PROMPT = CHART_PROMPT

    def build_tools(self, context: SubAgentContext) -> list[BaseTool]:
        # 无阅读工具：本子代理不经 deepagents task 派发执行
        return []
