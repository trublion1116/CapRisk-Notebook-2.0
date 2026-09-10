# Agent 集群技术选型调研(2026-09-10)

> 背景:拟用 agent 集群强化资金风险 AI 分析(核心观点提取)。本文调研 deepagents 实现 agent 集群的可行性,以及其他集群框架的取舍。
> 结论先行:**不换框架**。执行 `AGENT_V2_PLAN.md`(层0 代码调度器 + asyncio 并发 + create_react_agent worker),deepagents 保留在 chat/汇总层。

## 一、现状(读代码确认)

- `agent/` 是 FastAPI 服务,deepagents 0.7.11(已锁 uv.lock),底层就是 LangChain `create_agent` + LangGraph runtime。
- 现有提取流水线:编排 deepagent 用 `task` 工具逐批派发给 viewpoint-extraction 子代理(串行 7 批,LLM 决定分批),编排 LLM 自己合并终审。
- 子代理注册表(`app/subagents/base.py`)是干净的声明式开闭结构,新增视角/审查 agent 零改动注册。
- `AGENT_V2_PLAN.md` 已把 OpenAI Navier-Stokes 万 agent 案例的五个机制(代码编排、独立并行路线、提议/审查分离、确定性验证、中间结果蒸馏前传)映射成层0-层4 架构,**尚未执行**(git log 最后提交是 HANDOFF 文档)。

## 二、deepagents 能否做"agent 集群"

deepagents 定位是 **agent harness**(受 Claude Code 启发),核心能力:subagent 委派(`task` 工具)、虚拟文件系统、TodoList 规划、HITL 审批、上下文管理(summarize/offload)。

对集群场景的关键限制:

| 集群需要 | deepagents 提供 | 差距 |
|---|---|---|
| 确定性扇出(区间×视角网格) | `task` 派发由 LLM 自主决定 | 无法代码控制派发形状和覆盖度 |
| 并发控制(Semaphore/限流) | 无内建并发原语 | 需要自己 asyncio.gather |
| worker 间隔离 + 单向上报 | ✅ 子代理天然隔离上下文、单次汇报 | 这是它做对的部分 |
| 对抗审查循环(质询-答辩) | 无 | 代码层自建(V2 阶段5) |
| 确定性校验门(quote 子串) | 无 | 代码层自建(V2 阶段1) |
| 双跑自一致性/相似度合并 | 无 | 代码层自建(V2 阶段4c) |

结论:deepagents 的 `task` 子代理机制适合"LLM 自主委派"的深任务,不适合"代码精确控制覆盖度"的质量流水线。V2 计划的阶段2(去 LLM 化派发,直接用 `create_react_agent` + 子代理的 SYSTEM_PROMPT/tools 构造最小 worker)正是正确解法,且 `create_react_agent` 已在 deepagents 依赖里,零新增依赖。

deepagents 继续保留的位置:chat agent(意图识别+SSE 流式,已验证)、可选的最终轻量汇总 agent。

## 三、OpenAI 万 agent 案例的架构启示(与本项目对照)

OpenAI 的编排层是**代码 + 人类研究员调度**,不是一个 agent 框架:分组隔离、组内通信、研究员观察进展后资源再分配、Codex 蒸馏各组洞察回灌、Lean 形式化验证兜底。没有市售框架复刻这套东西——可迁移的是机制,不是产品。V2 计划的机制映射表(AGENT_V2_PLAN.md §1)已经做对了。

## 四、其他框架快评(针对本场景)

| 框架 | 判定 | 理由 |
|---|---|---|
| **LangGraph**(已在依赖树) | ✅ 用,但不新增 | worker 循环直接 `create_react_agent`;durable execution/checkpoint 对单机 30-60min job 非必需,V2 明确不引 langgraph-platform |
| CrewAI | ❌ | 角色制+LLM 编排,token 开销约 3x,无 checkpoint,确定性差——与"质量优先+覆盖度可控"相反 |
| AutoGen/AG2 | ❌ | 维护模式(并入 Microsoft Agent Framework),对话式协调不确定性高 |
| Microsoft Agent Framework | ❌ | Azure/.NET 生态导向 |
| OpenAI Agents SDK | ❌ | handoff 模型面向 OpenAI 生态;GLM 走 OpenAI 兼容层能用但 tracing 价值尽失,迁移零收益 |
| Claude Agent SDK | ❌ | 单循环 harness,多 agent 编排要自己组合——我们已有等价物(deepagents 就是它的开源对应) |
| Pydantic AI | ⚠️ 无必要 | 类型安全好,但对本流水线无增量价值 |
| Google ADK / Mastra / Letta | ❌ | Gemini/GCP 导向 / TS 导向 / 记忆导向,均不对口 |

行业共识(多份 2026 对比):生产级有状态工作流 → LangGraph;原型 → CrewAI。本项目已在 LangChain 栈上,且 V2 的"代码调度器"路线比任何框架的内置编排都更贴近 OpenAI 案例的实际做法。

## 五、建议路径

1. **执行 AGENT_V2_PLAN.md,从阶段1开始**(quote 校验 + SQLite job 持久化 + gold 评分基准)——这是量化回归的地基,先于任何扇出改造。
2. 阶段2 落地后,集群形态 = asyncio.gather + Semaphore(4) + create_react_agent worker 网格,GLM TPM 限流用信号量+快速失败兜住。
3. 不引入任何新框架依赖;每阶段 pytest 全绿 + 单独 commit。

## 参考来源

- deepagents 官方文档: https://docs.langchain.com/oss/python/deepagents/overview
- deepagents 仓库: https://github.com/langchain-ai/deepagents
- 框架对比(2026): shipsquad.ai / tokenmix.ai / uvik.net 等多份横向评测(交叉印证)
- OpenAI Navier-Stokes 公告: https://openai.com/index/navier-stokes-solution
