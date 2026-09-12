"""视觉模型（VLM）适配层——图表页 PNG → 结构化中文描述。

设计：文本主体仍走文本模型（成本与精度），视觉能力以独立 VLM 通道
接入（OpenAI 兼容 base64 直调，bigmodel glm-4.6v）。VLM 不可用时
优雅降级：图表照常提取落盘，描述标记不可用，主流程不受阻。

glm-4.6v 是 thinking 模型（reasoning 吃 token），max_tokens 给足。
"""

import base64
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app import config

logger = logging.getLogger(__name__)

# 图表解读 prompt：先事实后解读，逼模型复述坐标/图例/数值（可验证），
# 再做趋势解读；明确允许"不确定"，抑制幻觉。
CHART_PROMPT = """\
你是资金宏观研究团队的图表分析员。请解读这张来自 BIS 报告的页面截图（多为图表，\
也可能是含图表的正文页）。严格按以下结构输出：

1. **图表要素**：每个图的编号与标题（BIS 图注形如 "Graph 1.A)"）、坐标轴、\
图例、时间范围。若页面没有图表，直接说明"本页无图表"并简述页面内容。
2. **关键数值**：列出图中可读的关键数据点（端值、极值、拐点），标注不确定的\
读数。
3. **趋势与异常**：数据反映的趋势、结构性变化、异常点。
4. **一句话结论**：这张图对判断宏观/资金面意味着什么。

用中文输出。读不清的要素明确说"不确定"，不要编造数值。\
"""


def is_vision_available() -> bool:
    """VLM 通道是否已配置（base_url/key/model 齐全）。"""
    return bool(config.VISION_BASE_URL and config.VISION_API_KEY and config.VISION_MODEL)


def _build_vlm() -> ChatOpenAI:
    # bigmodel 的 glm-4.6v 默认开 thinking：实测全页图表 + 结构化输出时
    # reasoning 可拖到 170s+（接近超时）；关闭后 ~20-30s 出完整结构化描述，
    # 质量无损（图号/坐标/图例/数值齐全，实测 2026-09-11）。
    extra: dict = {}
    if "bigmodel" in (config.VISION_BASE_URL or ""):
        # bigmodel 私有参数须走 extra_body（顶层透传会被 openai SDK 拒绝）
        extra["extra_body"] = {"thinking": {"type": "disabled"}}
    return ChatOpenAI(
        model=config.VISION_MODEL,
        base_url=config.VISION_BASE_URL or None,
        api_key=config.VISION_API_KEY or None,
        max_tokens=2048,
        temperature=0.1,
        timeout=180.0,
        max_retries=1,
        **extra,
    )


async def describe_chart(image_path: str | Path, prompt: str | None = None) -> str:
    """单张图表 PNG → 结构化中文描述。失败抛异常，调用方负责降级。"""
    data = Path(image_path).read_bytes()
    b64 = base64.b64encode(data).decode()
    vlm = _build_vlm()
    message = HumanMessage(
        content=[
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            },
            {"type": "text", "text": prompt or CHART_PROMPT},
        ]
    )
    response = await vlm.ainvoke([message])
    content = response.content if isinstance(response.content, str) else str(response.content)
    if not content.strip():
        raise RuntimeError(f"empty VLM response for {image_path}")
    return content.strip()
