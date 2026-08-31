from deepagents import create_deep_agent
from langchain_core.tools import tool

from app.llm import build_llm
from app.on_client import OpenNotebookClient
from app.subagents import (
    SubAgentContext,
    registered_subagents,
    subagent_catalog,
)

CHAT_PREAMBLE = """\
你是"资金风险AI分析系统"的分析助手，服务于宏观经济报告（如 BIS 年度报告）的研究人员。

- 回答要基于系统提示中提供的笔记本上下文（源材料、笔记、洞察）。
- 引用具体材料时注明来源；如果上下文不足以回答，明确说明，不要编造。
- 用中文回答，专业、直接、有信息量。\
"""

ORCHESTRATOR_PROMPT_TEMPLATE = """\
你是资金风险分析系统的编排分析师（orchestrator），负责对一份权威宏观报告\
（如 BIS 年度经济报告）完成分析任务并将结果写回 OpenNotebook。

重要——数据流向说明：
- 报告共 {total_sections} 节，内容系统已准备好，由各子代理内部的阅读工具持有。
- 你没有任何读取报告的工具，也不需要。不要用 ls / glob / read_file 等文件工具\
寻找报告——虚拟文件系统里没有报告文件，那是死路。
- 你的职责只有一个入口：调用 task 把具体工作派给子代理。

分批派发策略（上下文安全，必须遵守）：
- 每次 task 只派一个分节区间（约 {batch_size} 节，如"请负责第 0-14 节，\
逐节读完并返回该区间候选"），区间按分节编号切分，覆盖全部 {total_sections} 节、\
不重不漏。
- 单次 task 让子代理读全部 {total_sections} 节是禁止的——会超出模型上下文。
- 收齐所有区间的结果后，进入终审。

可用子代理（通过 task 工具派发）：
{subagent_catalog}

工作流程（严格遵循）：
1. **规划**：用 todo 列出步骤（划分区间 → 逐批派发 → 合并终审 → 逐条提交 → 汇总）。
2. **逐批派发**：按区间调用 task，收集每批返回的候选清单。
3. **终审**：合并全部候选后去重与终审——相同观点合并、删除仍显套话或增量不足的\
条目、宁缺毋滥。
4. **提交**：对通过的每条调用 submit_insight 写回（目标 5~15 条）。\
quote 必须保持候选中的逐字原文引用，不得改写。
5. **汇总**：最终回答报告——共几批、收到多少条候选、提交多少条、最重要的 3 条是什么。\
"""


def build_orchestrator_prompt(
    total_sections: int, batch_size: int, catalog: list[str]
) -> str:
    """组装编排 prompt：子代理目录由注册表自动拼接（开闭原则）。"""
    return ORCHESTRATOR_PROMPT_TEMPLATE.format(
        total_sections=total_sections,
        batch_size=batch_size,
        subagent_catalog="\n".join(catalog),
    )


def build_chat_agent():
    """Chat agent used by the /chat endpoint (ON proxies notebook chat here)."""
    return create_deep_agent(
        model=build_llm(max_tokens=8192),
        system_prompt=CHAT_PREAMBLE,
        tools=[],
    )


def build_orchestrator_agent(
    client: OpenNotebookClient,
    sections: list,
    source_id: str,
    insight_type: str,
):
    """Lean orchestration agent: dispatch subagents, review, submit insights.

    Holds only the submission tool - all reading/analysis capabilities live
    in registered subagents (see app/subagents/).
    """
    total = len(sections)
    # 15 per batch: halves tokens-per-minute vs larger batches - GLM 1302
    # rate limits trigger during the reading phase otherwise (observed
    # 2026-08-31 with batches of 30).
    batch = min(15, total) if total else 1
    context = SubAgentContext(
        client=client,
        sections=sections,
        source_id=source_id,
        insight_type=insight_type,
    )
    prompt = build_orchestrator_prompt(total, batch, subagent_catalog())
    counter = {"submitted": 0}

    @tool
    async def submit_insight(
        quote: str,
        analysis: str,
        novelty: str,
        significance: str,
    ) -> str:
        """提交一条核心观点到 OpenNotebook（编排层统一提交，subagent 不提交）。

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
        model=build_llm(max_tokens=8192, temperature=0.2),
        tools=[submit_insight],
        subagents=registered_subagents(context),
        system_prompt=prompt,
    )
