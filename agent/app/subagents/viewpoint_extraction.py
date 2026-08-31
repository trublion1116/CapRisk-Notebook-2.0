"""Viewpoint extraction subagent.

Full-coverage reading + novelty filtering. Returns a structured candidate
list to the orchestrator - it does NOT write anything back to OpenNotebook
(submission is centralized in the orchestrator).
"""

from typing import Any

from langchain_core.tools import tool

from app.on_client import OpenNotebookClient

SYSTEM_PROMPT = """\
你是资深宏观金融分析师，负责对权威报告（如 BIS 年度报告）的**指定分节区间**做全覆盖\
阅读，并产出该区间的核心观点候选清单。

背景问题：AI 摘要倾向于提取"常见、模型熟悉"的内容，而报告中一句带过的关键信息\
（buried lede）才是真正的核心增量。你的流程必须刻意对抗这种偏差。

工作流程（严格遵循）：
1. **确认区间**：任务指令会指定你负责的分节区间（如"第 12-40 节"）。先调用 \
list_sections 查看分节，然后只用 read_section 逐节读完**区间内所有节**，\
区间外一律跳过，区间内不得跳过任何一节。
2. **候选收集**：阅读每节时识别候选关键句，重点关注：
   - 一句带过的风险提示或警告
   - 与主流叙事相反或明显偏离共识的表述
   - 新出现的数据拐点、结构性变化
   - 政策措辞的细微变化（收紧/放松、条件从句变化）
   - 脚注、图注、附录中的关键限定
3. **新颖性判断**：对每个候选判断它相对"市场共识/惯常宏观表述"的增量信息。\
套话、复述常识、纯背景介绍必须丢弃。
4. **返回清单**：最终回答输出且仅输出该区间的结构化候选清单（不要写其他总结），格式：

### 候选 1
- 原文引用: <逐字引用报告原文，英文原文保留英文>
- 解读: <中文：这句话实际在说什么>
- 新颖性: <相对市场共识/惯常表述的增量在哪里>
- 重要性: <对资金/风险判断意味着什么>

### 候选 2
...

目标每区间产出 3~8 条候选。编排层会合并各区间候选并终审提交——把所有有增量的\
候选都端上来，不要自行砍到过少。\
"""


def build(client: OpenNotebookClient, sections: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the subagent spec with tools bound to the job's sections."""

    @tool
    def list_sections() -> str:
        """列出报告全部分节及其开头预览，用于规划完整覆盖阅读。"""
        lines = []
        for s in sections:
            text = str(s["text"])
            preview = text[:80].replace("\n", " ")
            lines.append(f"[{s['index']}] ({len(text)} chars) {preview}...")
        return "\n".join(lines)

    @tool
    def read_section(index: int) -> str:
        """读取指定编号分节的全文。必须逐节读完所有分节，保证全覆盖。"""
        for s in sections:
            if s["index"] == index:
                return str(s["text"])
        return f"ERROR: section {index} not found"

    return {
        "name": "viewpoint-extraction",
        "description": (
            "对指定分节区间做全覆盖逐节阅读，返回该区间结构化核心观点候选清单"
            "（原文引用+解读+新颖性+重要性）。不写回数据。"
        ),
        "system_prompt": SYSTEM_PROMPT,
        "tools": [list_sections, read_section],
    }
