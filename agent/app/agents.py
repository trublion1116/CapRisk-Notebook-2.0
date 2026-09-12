import asyncio
import re

from deepagents import create_deep_agent
from langchain_core.tools import tool
from langgraph.config import get_stream_writer

from app.jobs import create_job
from app.llm import build_llm
from app.on_client import OpenNotebookClient
from app.subagents import (
    SubAgentContext,
    registered_subagents,
    subagent_catalog,
)

# markdown 链接/图片 URL 的前导不可见空白（含 thin space U+2009 等）
_URL_WS_RE = re.compile(r"\]\([\s\u200b\u2009\u200a\u202f\u00a0\ufeff]+")

CHAT_PREAMBLE = """\
你是"资金风险AI分析系统"的分析助手，服务于宏观经济报告（如 BIS 年度报告）的研究人员。

- 回答要基于系统提示中提供的笔记本上下文（源材料、笔记、洞察）。
- 引用具体材料时注明来源；如果上下文不足以回答，明确说明，不要编造。
- 用中文回答，专业、直接、有信息量。

意图识别——提取核心观点：
- 当用户要求"提取核心观点 / 提取观点 / 生成见解 / 分析这份报告的核心观点"等类似任务时，
  不要自己长篇摘抄，而是调用工具完成：
  1. 若用户未指明哪份报告或上下文中有多个来源，先调用 list_sources 确认目标；
  2. 调用 start_core_viewpoint_extraction —— 该工具会**同步执行**完整提取流水线，
     可能运行几分钟，期间系统会把执行细节（分批派发、逐条提交见解）实时展示给用户，
     你只需等待其返回，不要重复调用；
  3. 工具返回后，根据结果向用户汇报：处理了多少节、提交了多少条见解、任务状态。
- 普通问答、总结、分析类请求不需要调用工具，直接回答。\
"""

ORCHESTRATOR_PROMPT_TEMPLATE = """\
你是资金风险分析系统的编排分析师（orchestrator），负责对一份权威宏观报告\
（如 BIS 年度经济报告）完成核心观点提取，并将结果写回 OpenNotebook。

重要——数据流向说明：
- 报告共 {total_sections} 节，内容系统已准备好，由各子代理内部的阅读工具持有。
- 你没有任何读取报告的工具，也不需要。不要用 ls / glob / read_file 等文件工具\
寻找报告——虚拟文件系统里没有报告文件，那是死路。
- 你的职责：调用 task 派发 → 收齐候选 → 合成一篇结构化报告 → 一次性提交。

分批派发策略（上下文安全，必须遵守）：
- 每次 task 只派一个分节区间（约 {batch_size} 节，如"请负责第 0-14 节，\
逐节读完并返回该区间候选"），区间按分节编号切分，覆盖全部 {total_sections} 节、\
不重不漏。
- 单次 task 让子代理读全部 {total_sections} 节是禁止的——会超出模型上下文。
- 收齐所有区间的结果后，进入报告合成。

报告章节结构（数据面按报告真实结构切分，合成时按此组织）：
{outline}

可用子代理（通过 task 工具派发）：
{subagent_catalog}

工作流程（严格遵循）：
1. **规划**：用 todo 列出步骤（划分区间 → 逐批派发 → 合成报告 → 提交 → 汇总）。
2. **逐批派发**：按区间调用 task，收集每批返回的候选清单（候选自带出处与\
相关图表标注）。
3. **合成结构化报告**：调用 list_charts 查看图表资源，把全部候选组织成\
**一篇** markdown 报告，一级标题为 `# 《{source_title}》核心观点报告`。\
格式是硬性要求，逐条校对后再提交：

   F1. **引用角标**：每个观点/要点尾部带 markdown 链接角标 `[1](#ref-1)`、\
`[2](#ref-2)`...（编号从 1 连续递增，与 References 表一一对应；同一要点\
可带多个角标如 `[1](#ref-1)[2](#ref-2)`）。
   F2. **总述段**（紧跟一级标题）：2~4 句话概括报告核心主题与全篇主旨。
   F3. `## 一、报告整体框架与章节核心观点`：每章一个 `### N. 章标题（中文\
（英文原文））` 小节，章内用加粗要点列表：`* **要点名**：展开说明 [1](#ref-1)`，\
每章 2~4 条要点，写实质内容（数据、机制、结论），不写套话。
   F4. `## 二、核心 Box（专题研究）深度提炼`：按章分组（`### 第N章专题`），\
每个 Box 一条：`* **Box A：中文标题（英文原题）**：深度提炼段落，呈现其\
论点链条/量化结论/模型机制 [n](#ref-n)`。Box 是高价值来源，逐个覆盖。
   F5. `## 三、政策与市场含义`：跨章综合 2~4 条对资金面/政策的含义\
（素材不足可省略本段）。
   F6. 报告以 `## References（观点出处索引）` 收尾，表格列固定：
`| 编号 | 出处（章 / 节） | 页码 | 涉及图表 | 原文摘录 |`，首列为角标链接\
`[1](#ref-1)`，末列**逐字英文引用**（可直接定位原文的连续片段，40~120 字符，\
与正文观点的 > 引用一致），覆盖全部角标编号。
   F7. 图表在相关要点后嵌入（![图表说明](URL)，URL 从 list_charts 原样复制），\
全篇 8~15 张；Endnotes/References 章后内容不进报告。

   要点与 References 示例（照此格式）：

```
### 1. 第一章：进展与隐患（Progress and peril）

* **全球经济初显韧性**：尽管面临大幅加征关税，增长仍超预期——实际有效关税\
低于宣布值 [1](#ref-1)，叠加 AI 基础设施投资爆发提供的资本开支拉动 [2](#ref-2)。
* **AI 繁荣的脆弱性**：科技巨头资本开支已超自由现金流，"循环融资"积聚\
信用再定价风险 [3](#ref-3)。

## References（观点出处索引）

| 编号 | 出处（章 / 节） | 页码 | 涉及图表 | 原文摘录 |
|---|---|---|---|---|
| [1](#ref-1) | I. Progress and peril / A resilient start | p.17 | Graph 2.A | the effective average US tariff rate stabilised at 10% in the second half of 2025 |
| [2](#ref-2) | I. Progress and peril / AI progress | p.28 | — | AI-related investments contributed roughly one percentage point |
```

4. **提交前自检**：确认 F1~F7 全部满足（角标与 References 编号一一对应、\
原文摘录列存在）再调用 submit_report——**有且仅有一次调用**，content 为完整\
markdown。
5. **汇总**：最终回答报告——覆盖章节数、要点/Box 数、角标引用数、嵌入\
图表数、最重要的 3 条观点。
"""


def build_outline(sections: list) -> str:
    """sections → 章节大纲（注入编排 prompt，替代工具查询：零调用成本）。

    每章一行：章标题 + 节范围 + Box 清单。backmatter（Endnotes 等）只提示
    存在，报告不组织它们。
    """
    chapters: dict[str, list[dict]] = {}
    order: list[str] = []
    for s in sections:
        ch = str(s.get("chapter_title") or "（未分章）")
        if ch not in chapters:
            chapters[ch] = []
            order.append(ch)
        chapters[ch].append(s)
    lines = []
    for ch in order:
        secs = chapters[ch]
        idxs = [int(s["index"]) for s in secs]
        boxes = [s for s in secs if s.get("kind") == "box"]
        backm = [s for s in secs if s.get("kind") == "backmatter"]
        box_hint = (
            "；专题框：" + "、".join(str(b["title"]) for b in boxes) if boxes else ""
        )
        back_hint = f"（含 {len(backm)} 节章后内容，不进报告）" if backm else ""
        lines.append(f"- {ch}：第 {min(idxs)}-{max(idxs)} 节{box_hint}{back_hint}")
    return "\n".join(lines)


def build_orchestrator_prompt(
    total_sections: int,
    batch_size: int,
    catalog: list[str],
    outline: str = "",
    source_title: str = "报告",
) -> str:
    """组装编排 prompt：子代理目录由注册表自动拼接（开闭原则）。"""
    return ORCHESTRATOR_PROMPT_TEMPLATE.format(
        total_sections=total_sections,
        batch_size=batch_size,
        subagent_catalog="\n".join(catalog),
        outline=outline or f"- 共 {total_sections} 节（结构未识别，按节序组织）",
        source_title=source_title or "报告",
    )


def build_chat_agent():
    """Chat agent used by the /chat endpoint (ON proxies notebook chat here).

    Besides Q&A over the notebook context, it recognizes the "extract core
    viewpoints" intent and runs the extraction pipeline synchronously inside
    the tool call, streaming execution details to the user via the LangGraph
    stream writer.
    """
    # Deferred import: runner imports this module at top level
    from app.runner import run_extraction_job

    client = OpenNotebookClient()

    @tool
    async def list_sources() -> str:
        """列出 OpenNotebook 中的所有来源（id、标题、类型、见解数）。

        用于确定用户要对哪份报告做核心观点提取。
        """
        sources = await client.list_sources()
        if not sources:
            return "当前笔记本没有任何来源。"
        lines = []
        for s in sources:
            lines.append(
                f"- id={s['id']} | {s.get('title') or '(无标题)'} "
                f"| type={s.get('type')} | insights={s.get('insights_count')}"
            )
        return "\n".join(lines)

    @tool
    async def start_core_viewpoint_extraction(
        source_id: str,
        insight_type: str = "核心观点",
    ) -> str:
        """对指定来源执行核心观点提取（同步，可能运行几分钟）。

        执行细节（子代理分批阅读、逐条提交见解）会实时展示给用户，
        完成后见解自动写回 OpenNotebook。

        Args:
            source_id: 来源的 id（从 list_sources 获得）。
            insight_type: 写回 OpenNotebook 时的见解类型标签，默认"核心观点"。
        """
        try:
            writer = get_stream_writer()
        except RuntimeError:  # no stream context (e.g. direct invoke) - drop
            writer = lambda event: None

        job = create_job(source_id, insight_type)

        async def on_event(event: dict) -> None:
            try:
                writer(event)
            except Exception:  # noqa: S110, BLE001 - writer dies with the stream
                pass

        # shield: if the chat stream disconnects (user closes the page) the
        # HTTP task is cancelled, but the extraction keeps running to
        # completion in the background (queryable via GET /jobs/{job_id}).
        job_task = asyncio.create_task(run_extraction_job(job, on_event=on_event))
        try:
            await asyncio.shield(job_task)
        except asyncio.CancelledError:
            job.record("chat stream disconnected; extraction continues")
            raise

        return (
            f"提取任务结束: status={job.status}, "
            f"共 {job.sections} 节, job_id={job.id}"
            + (f", error={job.error}" if job.error else "")
            + "。执行过程已实时展示给用户，请基于以上结果向用户汇报。"
        )

    return create_deep_agent(
        model=build_llm(max_tokens=8192),
        system_prompt=CHAT_PREAMBLE,
        tools=[list_sources, start_core_viewpoint_extraction],
    )


def build_list_charts_tool(sections: list, source_id: str):
    """构造 list_charts 工具（模块级以便单测：deepagents 产物不暴露 tools）。"""
    from app import config

    @tool
    def list_charts() -> str:
        """列出全部图表页 PNG 及其 markdown 嵌入引用（合成报告时用）。

        每行：页码 | 所属章节 | 图表要点 | 可直接复制的 markdown 图片引用。
        """
        lines = []
        for s in sections:
            notes = s.get("image_notes") or []
            for i, img in enumerate(s.get("images") or []):
                name = str(img).rsplit("/", 1)[-1]
                # AGENT_PUBLIC_URL 为空时用相对路径，前端 Next rewrite
                # 把 /agent-images/* 同源代理到本服务（绕开浏览器直连 5060，
                # Windows→WSL 的 localhost 转发不可靠，实测 2026-09-12）
                if config.AGENT_PUBLIC_URL:
                    url = f"{config.AGENT_PUBLIC_URL}/images/{source_id}/{name}"
                else:
                    url = f"/agent-images/{source_id}/{name}"
                # alt 里不能有换行/中括号，会破坏 markdown 引用
                hint = (notes[i] if i < len(notes) else "").replace("\n", " ")[:70]
                hint = hint.replace("[", "(").replace("]", ")")
                lines.append(
                    f"- p{s.get('page_start', '?')} | {s.get('chapter_title', '')} | "
                    f"{hint} | ![{hint}]({url})"
                )
        return "\n".join(lines) if lines else "（本报告无图表页）"

    return list_charts


def build_orchestrator_agent(
    client: OpenNotebookClient,
    sections: list,
    source_id: str,
    insight_type: str,
    source_title: str = "",
):
    """Lean orchestration agent: dispatch subagents, synthesize one structured
    report, submit it in a single call.

    Holds only the report tools - all reading/analysis capabilities live
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
    prompt = build_orchestrator_prompt(
        total, batch, subagent_catalog(), build_outline(sections), source_title
    )
    list_charts = build_list_charts_tool(sections, source_id)

    @tool
    async def submit_report(content: str) -> str:
        """提交整篇结构化核心观点报告（完整 markdown，一次提交写回 OpenNotebook）。

        Args:
            content: 完整报告 markdown：# 标题 → ## 总览（索引表格）→ 各章
                （[PN] 编号观点、Box 子节、图表嵌入）→ ## References。这是
                唯一一次提交调用，确保整篇完整后再提交。
        """
        # LLM 生成 markdown 时会在 ]( 与 URL 之间插入不可见空白（实测
        # U+2009 thin space，2026-09-12），导致浏览器请求路径错误图裂。
        # 落库前代码层清洗，不依赖模型格式纪律。
        content = _URL_WS_RE.sub(r"](", content)
        result = await client.create_insight(source_id, insight_type, content)
        return f"OK: report created ({len(content)} chars, command_id={result.get('command_id')})"

    return create_deep_agent(
        model=build_llm(max_tokens=16384, temperature=0.2),
        tools=[list_charts, submit_report],
        subagents=registered_subagents(context),
        system_prompt=prompt,
    )
