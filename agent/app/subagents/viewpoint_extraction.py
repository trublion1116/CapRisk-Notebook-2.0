"""观点提取子代理。

对报告的**指定分节区间**做全覆盖阅读，返回结构化候选清单。
不写回数据——提交统一在编排层（见 app/agents.py）。

分节由数据面（app/ingestion）产出，带章节结构：每节有所属章
（chapter_title）、类型（body 正文 / box 专题框 / backmatter 章后
内容）和该节页面的图表描述（image_notes，VLM 预生成）。
"""

from langchain_core.tools import BaseTool, tool

from app.subagents.base import BaseSubAgent, SubAgentContext

_KIND_LABEL = {"body": "正文", "box": "专题框", "backmatter": "章后", "frontmatter": "前言"}


class ViewpointExtractionSubAgent(BaseSubAgent):
    name = "viewpoint-extraction"
    description = (
        "对指定分节区间做全覆盖逐节阅读，返回该区间结构化核心观点候选清单"
        "（原文引用+解读+新颖性+重要性+章节）。不写回数据。"
    )

    SYSTEM_PROMPT = """\
你是资深宏观金融分析师，负责对权威报告（如 BIS 年度报告）的**指定分节区间**做全覆盖\
阅读，并产出该区间的结构化观点候选清单。

背景问题：AI 摘要倾向于提取"常见、模型熟悉"的内容，而报告中一句带过的关键信息\
（buried lede）才是真正的核心增量。你的流程必须刻意对抗这种偏差。

报告结构说明（list_sections 可见元数据）：
- 每节标注 [所属章 | 本节标题 | 类型]。类型含：正文、**专题框（Box，BIS 报告用\
框线框出的专题深挖，信息密度高于正文）**、章后（Endnotes/References，价值低）、前言。
- 部分节附带**图表解读**（read_section 的附录）——这是视觉模型对该节页面图表的\
结构化描述（图号/图例/关键数值/趋势）。图表是观点的重要来源，同样按候选流程处理。

工作流程（严格遵循）：
1. **确认区间**：任务指令会指定你负责的分节区间（如"第 12-40 节"）。先调用 \
list_sections 查看分节，然后只用 read_section 逐节读完**区间内所有节**，\
区间外一律跳过，区间内不得跳过任何一节（章后类型可快速扫描，不必深挖）。
2. **候选收集**：阅读每节时识别候选关键句，重点关注：
   - 一句带过的风险提示或警告
   - 与主流叙事相反或明显偏离共识的表述
   - 新出现的数据拐点、结构性变化
   - 政策措辞的细微变化（收紧/放松、条件从句变化）
   - 脚注、图注、附录中的关键限定
   - **专题框（Box）**：按完整专题对待，提取其核心论点链条（背景→机制→结论），\
   不要只挑单句
   - **图表**：图表解读中的关键数值与趋势拐点，注意与正文叙述相互印证或矛盾之处
3. **新颖性判断**：对每个候选判断它相对"市场共识/惯常宏观表述"的增量信息。\
套话、复述常识、纯背景介绍必须丢弃。
4. **返回清单**：最终回答输出且仅输出该区间的结构化候选清单（不要写其他总结），格式：

### 候选 1
- 原文引用: <逐字引用报告原文，英文原文保留英文；图表候选写"图注/图表要素+关键数据">
- 解读: <中文：这句话实际在说什么>
- 新颖性: <相对市场共识/惯常表述的增量在哪里>
- 重要性: <对资金/风险判断意味着什么>
- 章节: <该候选所属的章与本节标题，如 "I. Progress and peril / Box A: ...">
- 相关图表: <该候选涉及的图表：图号（如 Graph 7.B）或"本节图表解读 2"；无则省略本行>

### 候选 2
...

目标每区间产出 3~8 条候选。编排层会把各区间候选**合成一篇按章节组织的结构化报告**\
（并在相关观点处嵌入图表）——把所有有增量的候选都端上来，不要自行砍到过少。\
"""

    def build_tools(self, context: SubAgentContext) -> list[BaseTool]:
        sections = context.sections

        @tool
        def list_sections() -> str:
            """列出报告全部分节及其章节/类型/页码，用于规划完整覆盖阅读。"""
            lines = []
            for s in sections:
                text = str(s["text"])
                preview = text[:60].replace("\n", " ")
                kind = _KIND_LABEL.get(str(s.get("kind", "body")), "正文")
                n_imgs = len(s.get("images") or [])
                img_hint = f" 图表x{n_imgs}" if n_imgs else ""
                lines.append(
                    f"[{s['index']}] {s.get('chapter_title', '')} | "
                    f"{s.get('title', '')} | {kind} | "
                    f"p{s.get('page_start', '?')}-{s.get('page_end', '?')}{img_hint} | "
                    f"({len(text)} chars) {preview}..."
                )
            return "\n".join(lines)

        @tool
        def read_section(index: int) -> str:
            """读取指定编号分节的全文（含该节页面的图表解读附录）。

            必须逐节读完所有分节，保证全覆盖。
            """
            for s in sections:
                if s["index"] == index:
                    parts = [str(s["text"])]
                    notes = s.get("image_notes") or []
                    if notes:
                        parts.append("\n\n--- 本节页面图表解读（视觉模型预生成） ---")
                        parts.extend(
                            f"\n[图表 {i + 1}]\n{note}" for i, note in enumerate(notes)
                        )
                    return "\n".join(parts)
            return f"ERROR: section {index} not found"

        return [list_sections, read_section]
