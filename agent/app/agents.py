from deepagents import create_deep_agent
from langchain_core.tools import tool

from app.llm import build_llm
from app.on_client import OpenNotebookClient

CHAT_PREAMBLE = """\
你是"资金风险AI分析系统"的分析助手，服务于宏观经济报告（如 BIS 年度报告）的研究人员。

- 回答要基于系统提示中提供的笔记本上下文（源材料、笔记、洞察）。
- 引用具体材料时注明来源；如果上下文不足以回答，明确说明，不要编造。
- 用中文回答，专业、直接、有信息量。\
"""

EXTRACTION_PROMPT = """\
你是资深宏观金融分析师，任务是从一份权威报告（如 BIS 年度报告）中提取真正核心的观点。

背景问题：AI 摘要倾向于提取"常见、模型熟悉"的内容，而报告中一句带过的关键信息\
（buried lede）才是真正的核心增量。你的流程必须刻意对抗这种偏差。

工作流程（严格遵循）：
1. **全覆盖阅读**：先调用 list_sections 查看全部分节，然后用 read_section 逐节读完\
所有分节，不得跳过任何一节。
2. **候选收集**：阅读每节时识别候选关键句，重点关注：
   - 一句带过的风险提示或警告
   - 与主流叙事相反或明显偏离共识的表述
   - 新出现的数据拐点、结构性变化
   - 政策措辞的细微变化（措辞收紧/放松、条件从句的变化）
   - 脚注、图注、附录中承载的关键限定信息
3. **新颖性判断**：对每个候选判断它相对于"市场共识/惯常宏观表述"是否包含增量信息。\
套话、复述常识、纯背景介绍必须丢弃。
4. **提交**：只对真正核心的观点调用 submit_insight，目标 5~15 条，宁缺毋滥。\
quote 必须逐字引用报告原文；原文为英文时 quote 保留英文，analysis 用中文撰写。
5. **收尾**：完成后输出简要总结：共提取多少条、最重要的 3 条是什么、为什么重要。\
"""


def build_chat_agent():
    """Chat agent used by the /chat endpoint (ON proxies notebook chat here)."""
    return create_deep_agent(
        model=build_llm(),
        system_prompt=CHAT_PREAMBLE,
        tools=[],
    )


def build_extraction_agent(
    client: OpenNotebookClient,
    sections: list[dict[str, object]],
    source_id: str,
    insight_type: str,
):
    """Extraction agent with full-coverage reading and insight write-back tools."""

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

    counter = {"submitted": 0}

    @tool
    async def submit_insight(
        quote: str,
        analysis: str,
        novelty: str,
        significance: str,
    ) -> str:
        """提交一条核心观点到 OpenNotebook。

        Args:
            quote: 报告原文的逐字引用（英文原文保留英文）。
            analysis: 中文解读：这句话实际在说什么。
            novelty: 相对市场共识/惯常表述的新颖之处（增量在哪里）。
            significance: 为什么这句话重要（对资金/风险判断的含义）。
        """
        content = (
            f"> {quote}\n\n"
            f"{analysis}\n\n"
            f"**新颖性**: {novelty}\n\n"
            f"**重要性**: {significance}"
        )
        result = await client.create_insight(source_id, insight_type, content)
        counter["submitted"] += 1
        return (
            f"OK: insight #{counter['submitted']} created "
            f"(command_id={result.get('command_id')})"
        )

    return create_deep_agent(
        model=build_llm(temperature=0.2),
        tools=[list_sections, read_section, submit_insight],
        system_prompt=EXTRACTION_PROMPT,
    )
