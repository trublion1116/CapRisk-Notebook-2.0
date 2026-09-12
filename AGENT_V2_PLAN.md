# Agent V2 升级实施方案（可直接执行版）

> 写给零上下文的执行者（人或大模型）。本文自包含：背景、目标架构、分阶段任务、每步的改动点与验收标准、环境陷阱。
> 配套文档：《HANDOFF.md》（项目来龙去脉）、《启动指南.md》（部署命令）、架构图 `agent-architecture-v2.html`。
> 设计参照：OpenAI 2026-09 Navier-Stokes 万 agent 案例的五个可迁移机制（代码编排、独立并行路线、提议/审查分离、确定性验证、中间结果蒸馏前传）。

---

## 0. 执行前必读

### 0.1 项目是什么

资金风险 AI 分析系统，分析 BIS 年度经济报告等宏观报告，提取核心观点写回 OpenNotebook。

- `open-notebook/` — 主项目（fork 自 lfnovo/open-notebook v1.14.0，docker compose 跑，API :5055，旧前端 :8502）
- `agent/` — 独立 FastAPI 服务（deepagents 框架，uv 管理，端口 :5060）——**本次改造对象**
- 前端 dev server :3001（新功能都在这里）
- langfuse :3000（tracing）、SurrealDB（ON 的库）

### 0.2 当前提取流水线（改造前）

```
用户在 chat 说"提取核心观点"
→ agent /chat/stream 的 chat agent 识别意图，调工具 start_core_viewpoint_extraction（同步执行）
→ runner.run_extraction_job：取正文 → split_sections（固定 6000 字符切分）→ build_orchestrator_agent
→ 编排 LLM（deepagents）逐批 task 派发给 viewpoint-extraction 子代理（每批 15 节，串行 7 批）
→ 编排 LLM 自己合并去重终审 → 逐条 submit_insight 写回 ON
→ 全程 custom 流事件经 SSE 透传到前端 ToolCallTray 卡片
```

### 0.3 关键文件地图（agent/ 下）

| 文件 | 职责 |
|---|---|
| `app/agents.py` | chat agent（意图识别+触发工具）、编排 agent（task 派发+submit_insight）、两个 prompt 模板 |
| `app/runner.py` | run_extraction_job 主流程：取文本→切片→跑编排 agent |
| `app/extraction.py` | PDF→文本、split_sections（6000 字符段落切分）——**要被数据面替代** |
| `app/subagents/base.py` | BaseSubAgent 基类 + SubAgentContext + 自动注册表（pkgutil 发现，开闭原则） |
| `app/subagents/viewpoint_extraction.py` | 唯一现有子代理：区间全覆盖阅读，返回结构化候选 |
| `app/subagents/__init__.py` | 包内模块自动发现注册 |
| `app/jobs.py` | 内存 job 表（**重启即丢，要换 SQLite**） |
| `app/llm.py` | build_llm()，ChatOpenAI，bigmodel GLM 文本模型 |
| `app/on_client.py` | OpenNotebook HTTP client |
| `app/chat_stream.py` | 流事件解析 |
| `app/tracing.py` | langfuse handler |
| `tests/` | pytest（pythonpath=.，asyncio_mode=auto），有 test_subagents/test_runner 等 |

### 0.4 环境陷阱（WSL，反复咬人，必须遵守）

1. **WSL /mnt/c 的 inotify 失效**：改 agent 代码后 `--reload` 检测不到，必须重启 agent 服务；改前端后必须重启 dev server + 浏览器硬刷新。
2. agent 重启方式：`scripts/agent-start.sh`（setsid nohup，日志 `/tmp/opencode/agent.log`）；杀进程用 `for p in $(pgrep -f ...); do kill -9 $p; done`（pkill 会匹配 shell 自身导致挂起）。
3. 交互式调试：`cd agent && make dev`。
4. ON compose 启动必须带 `--pull never`；compose 服务名是 `open_notebook`（下划线）。
5. 流式请求前端必须直连 5055（dev 的 /api rewrite 会缓冲 SSE），勿改回相对路径。
6. LLM 是 bigmodel（智谱）OpenAI 兼容接口，`.env` 里 LLM_API_KEY/LLM_BASE_URL。GLM 有 TPM 限流前科（2026-08-31 观测），代码里 `max_retries=1` 是故意的（快速失败，不要调大）。
7. **用户对速度无要求，质量优先**：可以一份报告跑 30-60 分钟，但不接受为省 token 牺牲覆盖度或校验强度。
8. 遵守 repo 风格：中文注释/docstring 说明"为什么"，工具 docstring 写清楚用途（会进 prompt），新子代理走注册表自动发现，不要改既有注册机制。

---

## 1. 目标架构（总览）

分层数据流（自上而下）：

```
层0  代码调度器（runner.py，确定性代码，非 LLM）
      fetch → 数据面 → 生成任务网格 → asyncio.gather + Semaphore(3~4)
数据面（纯代码，pkgutil 自动注册，扩展=新建模块文件）
      切片策略链（outline书签→字号/正则→字符兜底，自动降级）
      图片提取（pypdfium2 按页渲染 300DPI PNG，按页码归属 section）
      context pack（1 次 LLM 速览全报告 → 主题地图 + 市场共识基线，全场共享）
层1  并行 worker 池
      文本：区间 × 3 视角扇出（buried-lede / 逆共识 / 数据拐点），双跑自一致性
      图表：chart-reader，per-image 派发，双通道交叉验证
层2  对抗审查（adversarial-review，独立 agent，循环质询直到收敛，候选去标识）
层3  确定性校验（quote ∈ 原文子串，代码门，失败拒提交）
层3.5 证据核验（核验 agent：解读是否超出原文支持范围的外推）
层4  submit_insight 逐条写回 ON + 最终汇报
```

OpenAI 案例机制 → 本架构映射：

| 机制 | 落点 |
|---|---|
| 编排是代码不是 LLM | 层0 调度器 |
| 并行独立路线互不可见 | 3 视角 worker + 双跑，worker 间不共享产出 |
| 提议与审查分离 | 层2 独立对抗审查（去标识防锚定） |
| Lean 式确定性验证 | 层3 子串校验 + 层3.5 证据核验 |
| 中间结果蒸馏前传 | context pack 注入全部 worker |

动态扩展点（代码级开闭，不是运行时热插拔）：

- 切片策略：`app/ingestion/` 新建模块即注册
- 子代理：`app/subagents/` 新建模块即注册；BaseSubAgent 增加 `dispatch` 类属性（`"range" | "document" | "per-image"`），调度器按声明决定派发形状
- SubAgentContext 加字段（带默认值）即全员可见

---

## 2. 分阶段任务

按顺序执行，每阶段独立可测、可提交。**每阶段完成后跑 `cd agent && uv run pytest`，全绿才算完成。**

### 阶段 1：层3 quote 校验 + job SQLite 持久化（地基，无架构改动）

**1a. quote 逐字校验**

新文件 `app/validation.py`：

```python
def normalize(s: str) -> str:
    # 归一化：去除所有空白、统一连字符（‐-–—→-）、统一引号（‘’“”→'"）、lowercase 不做（英文原文大小写有意义，但 BIS 引文做 casefold 更宽容——实现时选 casefold，注释说明）
```

- `verify_quote(quote: str, source_text: str) -> bool`：normalize 后判断 `quote in source_text`。
- 在 `agents.py` 的 `submit_insight` 工具内调用：不通过则返回错误信息要求重新逐字引用（不抛异常，让编排 LLM 能重试）；重试 2 次仍失败则该条丢弃并记入 job log。
- `sections` 的全文拼接作为 source_text（runner 里传给编排 agent 构造时）。
- 测试：`tests/test_validation.py`——正常引用、换行/多空格差异、Unicode 连字符/引号差异、改写过的引用（必须 False）、空 quote（False）。

**1b. job 持久化**

改造 `app/jobs.py`：SQLite（`agent/data/jobs.db`，标准库 sqlite3，不引新依赖）：

- 表：jobs(id, source_id, insight_type, status, error, source_title, sections, trace_id, started_at, finished_at, log TEXT /* json array */)
- `create_job` 写入，`record` 追加（内存对象保留，写穿到 DB），status 变化时 UPDATE
- 服务重启后 `get_job` 从 DB 读（log 反序列化）
- 不做正在运行任务的断点续跑（重启丢正在跑的 job 可接受，但历史记录必须不丢）
- 测试：创建→record→查→重新实例化 store→查（模拟重启）

### 阶段 1c：自动评分（gold 基准 + 无 LLM 匹配器 + 落库）

> 依赖 1a 的 normalize、1b 的 SQLite。此后每次提取完成自动产出 recall/precision，改任何 prompt/模型/阈值后重跑提取即得对比数字——这是整个 V2 的量化回归基准。

**1c-1. Gold 标准库（一次性建设）**

- 目录 `agent/eval/gold/{source_id}.json`，每条：`{"quote": "...", "analysis": "...", "tags": [...]}`。
- 转换脚本 `scripts/import_gold.py`：读专家分析报告（md/pdf），解析出观点条目（格式自适应，输出解析结果让用户确认一次再落盘）。
- **gold 逐条过 1a 的 quote 子串校验**：专家引用若与原文对不上（对不上时报告差异最大的前几条让用户裁决），该条标注 `quote_verified: false`，匹配时降权使用。gold 本身有噪声会污染所有后续指标，这一步必须做。

**1c-2. 评分核心 `app/evaluator.py`（纯函数，无 LLM，可复现）**

- 匹配两级：
  1. quote 归一化（复用 1a normalize）后重叠率 > 0.5 → 判同源（同一原文段落）
  2. 兜底：quote+analysis 拼接的 n-gram 相似度（阈值初始 0.6，可用 gold-agent 真实对校准）
- 指标：recall（gold 观点被命中的比例）、precision（agent 见解对上 gold 或已知合理增量的比例）、F1、matched 明细。
- 输入输出全是数据结构，不打印不定性；测试 `tests/test_evaluator.py`（构造金标准对/边界 case：换行差异、部分引用、同段不同观点）。

**1c-3. 自动触发（挂进提取主流程）**

- `run_extraction_job` 的 finally 块：job 成功后查 `agent/eval/gold/{source_id}.json` 是否存在，存在则：取本次写入 ON 的见解（submit_insight 时已持有，直接传列表，不回查 API）→ 跑 evaluator → 入库 → job.log 追加 `"eval: recall=0.73 precision=0.81 f1=0.77"`。
- 无 gold 的 source 跳过评分，行为不变。

**1c-4. 落库与查询（接 1b 的 SQLite，同库新表）**

```sql
CREATE TABLE evals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  recall REAL, precision REAL, f1 REAL,
  matched_count INTEGER, gold_count INTEGER, agent_count INTEGER,
  detail_json TEXT  -- 每对匹配明细（gold 条、agent 条、匹配方式、分数）
);
```

- 新端点 `GET :5060/evals?source_id=...`（历史序列，按 created_at 排序）、`GET :5060/evals/latest`（每个 source 最新一条）。
- 每次评分后自动重写 `agent/eval/report.md`：各 source 最近 N 次的指标趋势表。

**1c-5. 增量清单（人工环节压到最小，可选）**

- agent 有 / gold 无的条目落 `agent/eval/pending/{job_id}.md`。
- 用户有空时标注"真增量/噪音"（追加到 `agent/eval/gold/{source_id}.json` 的 `accepted_extras` 字段或标记文件）。
- 已标注为真增量的条目在后续 precision 计算中计为合理；不做这步系统照常跑，只是 precision 偏保守。
- **注意 recall 上限不是 100%**：gold 是专家抽样非穷举。指标的用法是跨配置/跨阶段看趋势对比，不是看绝对值。

### 阶段 2：层0 代码调度器（去 LLM 化派发）

改造 `app/runner.py` + 新文件 `app/dispatcher.py`：

- 现状：编排 deepagent 用 task 工具逐批派发（串行、LLM 决定分批）。改为：**代码生成批次网格，worker 并发执行，最后只保留一个轻量 LLM 汇总**。
- worker 实现：不经过 deepagents 的 task 机制，直接用 `build_llm()` + 子代理的 tools 构造最小 agent（复用 `ViewpointExtractionSubAgent.SYSTEM_PROMPT` 和 `build_tools(context)`，即 `create_react_agent` 或 langgraph 的最小循环——**用 langgraph.prebuilt.create_react_agent，deepagents 依赖里已有**）。
- 派发：`asyncio.gather` + `asyncio.Semaphore(4)`，每批一个 HumanMessage："请负责第 X-Y 节，逐节读完并返回该区间候选清单"。
- 每个 worker 的返回（候选清单文本）聚合后交给轻量终审 agent（见阶段 5 前的临时方案：暂用现有编排 prompt 的第 3-5 步构造一个只有 submit_insight 工具的 deepagent 做合并终审提交——**此为过渡，阶段 5 替换**）。
- 流事件：worker 启动/完成各发一个 custom 事件（`{"type": "tool_call", "name": "worker", "args": {"batch": "0-14", "index": 3}}` / 对应 tool_result），复用 `events_from_update` 的事件形状，前端不用改。
- 异常隔离：单 worker 失败不炸整job，记录该区间失败、其余继续（gather return_exceptions=True），最终汇报包含失败区间。
- job log 记录每批耗时。
- 测试：mock LLM（现有 test_runner 的做法），验证网格生成、并发执行、异常隔离、事件序列。

### 阶段 3：数据面 ingestion（章节切片 + 图片管线）

新包 `app/ingestion/`，仿照 `app/subagents/` 的自动注册：

```
app/ingestion/__init__.py   # pkgutil 自动发现（照抄 subagents/__init__.py 模式）
app/ingestion/base.py       # SegmentationStrategy 接口 + 注册表
app/ingestion/outline.py    # pypdf reader.outline → 按书签切（首选）
app/ingestion/heading.py    # 字号（需 pdfium 文本对象）或正则启发（^数字+空格+Title Case / ^Chapter \d / 罗马数字节号）→ 次选
app/ingestion/char_fallback.py  # 现有 split_sections 逻辑降级为兜底
app/ingestion/images.py     # 图片提取（见下）
```

- `SegmentationStrategy` 接口：`name`、`can_handle(pdf_or_text) -> bool`（或置信度）、`segment(...) -> list[Section]`。
- 策略链：按序尝试 outline → heading → char_fallback，选第一个产出合理结果的（合理 = 节数在 3~200 且平均节长 > 500 字符，否则降级）。
- **Section 结构升级**（新 dataclass，向后兼容现有 dict 用法或统一替换）：`{index, text, chapter_title, level, page_range, images: list[str] /* PNG 路径 */}`。
- **图片提取的关键选型：不要用 pypdf 提嵌入图片**——BIS 图表是矢量图，嵌入图提取拿到的是空手或碎片。用 `pypdfium2` 把图表所在页渲染成 300 DPI PNG（新增依赖 `pypdfium2>=4`，纯 wheel 无系统依赖）。图表页判定：文本层含 "Graph/Chart/Figure/Table" 图注模式的页 + 该页图像/绘图对象密度高（pdfium 页面对象统计，vector path 数量阈值），拿不准就整页渲染交给后面的视觉模型判（image_analysis 自带分类）。
- 图片落盘：`agent/data/images/{source_id}/{page}_{n}.png`，路径挂到 Section.images。
- ON 已解析 full_text 路径（on-parsed）没有 PDF 时：图片管线跳过（只能 PDF 路径才有），job log 记录 "images skipped: no pdf"。
- `runner.py` 的 `fetch_source_text` 改为返回 (pdf_bytes | None, text, origin)，数据面统一入口 `ingest(pdf_bytes, text) -> list[Section]`。
- 测试：构造小 PDF（reportlab 或手工 fixture）验证 outline 切分、降级链、图注页识别；`tests/test_ingestion.py`。真实回归：用 `showcase/` 或仓库里已有的 ar2026e-cut.pdf（问用户要路径）跑一次人工检查切分结果。

### 阶段 4：context pack + 三视角扇出

**4a. context pack（新子代理 `app/subagents/context_pack.py`，dispatch="document"）**

- 输入：全部分节的**首 200 字符拼接**（不是全文——它只做地图不做提取）。
- 输出（结构化）：报告主题地图（各章主题一句话）+ 市场共识基线（10-20 条"这类报告的惯常表述"清单）。
- 在 dispatcher 里最先执行（1 次 LLM 调用），结果注入所有 worker 的 prompt 前缀："以下是报告地图与市场共识基线，你的新颖性判断以此为参照：..."
- 缓存：per source_id 存 `agent/data/packs/{source_id}.json`，重复提取复用（手动失效：文件删除即可）。

**4b. 三视角 worker**

新文件 `app/subagents/perspectives.py`（或每视角一个模块，遵守注册表模式）：

- `buried-lede-hunter`：一句带过的风险提示/警告（继承现有 viewpoint_extraction prompt 的核心）
- `counter-consensus`：与主流叙事相反或明显偏离共识的表述
- `data-inflection`：数据拐点、结构性变化、政策措辞细微变化（收紧/放松、条件从句）

每个都是 dispatch="range" 的注册子代理，SYSTEM_PROMPT 沿用现有候选输出格式（### 候选 N / 原文引用/解读/新颖性/重要性）。dispatcher 的网格变为 区间 × 视角，Semaphore 保持 4。

**4c. 双跑自一致性（质量优先模式）**

- 每个视角跑两遍：temperature 0.2 和 0.7（`build_llm` 加 temperature 参数已支持）。
- 合并时做语义相似度匹配（不引 embedding API——用文本手段：quote 归一化后精确匹配 + analysis 的 n-gram 重叠率 > 0.6 视为同条）。两轮都出现 → 绿色通道（标记 high-agreement）；单轮出现 → 进入层2 重点盘问清单。
- job log 记录双跑的候选数与匹配数。

### 阶段 5：层2 对抗审查循环 + 层3.5 证据核验（终审重构）

**5a. 对抗审查子代理 `app/subagents/adversarial_review.py`（dispatch="document"）**

- 输入：全部候选（**去标识**：去掉视角/批次来源标记，随机打乱顺序，防锚定）+ context pack。
- 职责：逐条攻击——quote 是否断章取义、novelty 是否只是措辞差异、significance 是否脑补、单轮候选（低一致性）重点盘问。
- 输出：每条 verdict（pass / reject / challenge + 质疑理由）。
- **循环**：challenge 的候选连同质疑理由发回对应对角重新答辩（worker 拿着质疑 + 原文段落重读再回答），最多 2 轮；仍 challenge 则 reject。循环在 dispatcher 代码里控制（不是 LLM 自循环）。
- 通过的候选进入层3 子串校验 → 层3.5。

**5b. 证据核验子代理 `app/subagents/evidence_check.py`（dispatch="document"）**

- 输入：候选的 (quote 所在原文段落, analysis 字段)。不给 novelty/significance（聚焦"解读是否忠于原文"）。
- 判断：analysis 中每个事实性断言是否能从原文段落支持。标记 unsupported claims。
- 有 unsupported 断言 → 该条降级为 reject 或要求 worker 修订 analysis（复用 5a 的循环通道）。

**5c. 终审编排简化**

阶段 2 的"过渡终审 deepagent"退位：现在终审全由 5a/5b/层3 组成，最后只留一个纯代码的提交循环（submit_insight 变为 dispatcher 直接调 `client.create_insight`，或保留工具形式但由代码逐条调用）+ 一个轻量 LLM 汇总（生成最终中文汇报：批次/候选/提交/最重要 3 条）。`agents.py` 里的 ORCHESTRATOR_PROMPT_TEMPLATE 大部分作废，注意保留 chat agent（CHAT_PREAMBLE 不动）。

### 阶段 6：chart-reader + 视觉模型评测

**6a. 视觉 provider 适配层 `app/vision.py`**

统一接口：`describe_chart(image_path: str, prompt: str | None) -> str`，两个实现 + 配置开关（`.env` 的 VISION_PROVIDER）：

- `glm_api`：ChatOpenAI 兼容直调 `glm-4.6v`（llm.py 加 build_vlm(model=...)，image 以 base64 data URL 传）。prompt 完全可控。
- `zai_mcp`：MCP 通道。用 `mcp` python sdk（stdio client）spawn `npx -y @z_ai/mcp-server@latest`，env `Z_AI_API_KEY`（.env 已有 LLM_API_KEY 复用，注意 bigmodel key 兼容性，文档：docs.bigmodel.cn/cn/coding-plan/mcp/vision-mcp-server）。工具优先 image_analysis（可带自定义 prompt），analyze_data_visualization 作为无 prompt 备选。

**6b. 评测脚本 `scripts/eval_vision.py`（先跑这个再定生产通道）**

- 从测试 PDF 渲染 20-30 张真实 BIS 图表 PNG（复用 ingestion/images.py）。
- 每张图跑 3 通道：MCP analyze_data_visualization / MCP image_analysis+定制 prompt / glm-4.6v 直调+定制 prompt。
- 定制 prompt 模板（硬编码在脚本里）："先复述坐标轴、图例、时间范围，再列出图中关键数值，最后解读趋势与异常。不确定的明确说不确定。"
- 输出对照表 md（图 × 通道 → 结构化结果），人工盲评。
- 评测结论决定生产配置：若 image_analysis+prompt 与 glm-4.6v 质量相当 → 生产用 MCP（用户 MCP 额度充足）；差距大 → 复杂图走 API。

**6c. chart-reader 子代理 `app/subagents/chart_reader.py`（dispatch="per-image"）**

- dispatcher 对 Section.images 逐图派发（不占文本区间网格的并发槽，独立 Semaphore(2)，视觉调用慢）。
- 双通道交叉验证（质量优先）：MCP 和 API 各解析一次，结构化结果一致性高（关键数值/趋势方向一致）→ 采信；冲突 → 追加 GLM-4.1V-Thinking-Flash（免费）仲裁或标记"存疑图表"两版解读都带出。
- 产出并入候选流：图表候选的 quote 字段用"图注 + 图中关键数据"格式（图不是文本，层3 子串校验对图表候选跳过，改为校验图注部分 ∈ 原文）。

---

## 3. 全局验收标准

1. `cd agent && uv run pytest` 全绿；`uv run ruff check .` 无新告警。
2. 端到端：对真实 BIS 报告（如 ar2026e-cut.pdf）在 3001 前端触发"提取核心观点"，全程 SSE 事件流可见（worker 进度/逐条提交），见解入库，`GET :5060/jobs/{id}` 可查且重启 agent 后历史 job 仍在。
3. 质量抽检：随机抽 10 条已提交见解，quote 全部能在原文中逐字找到（层3 保证）；analysis 无原文不支持的外推（层3.5 保证，抽检确认）。
4. 覆盖度：job log 显示全部区间 × 全部视角执行完成（或明确记录失败区间）；图表数量与报告实际图表数一致。
5. 每阶段一个 git commit，格式沿用 repo 风格（`feat:/fix:` + 中文说明）。

## 4. 明确不做的事（防止执行者自由发挥）

- 不做多 agent 互看结果的"讨论"模式（破坏视角独立性）。
- 不做三遍以上自一致性（两遍+对抗审查已覆盖主要错误模式）。
- 不做运行时热插拔/插件加载（WSL inotify 环境下是伪需求）。
- 不改 chat agent 的意图识别和 SSE 透传链路（已验证可用）。
- 不动 open-notebook/ 主项目（除非阶段 3 发现 ON API 缺口，需先报告）。
- 不为速度做任何优化牺牲：质量优先模式已确认，墙钟 30-60 分钟/份可接受。
- 不引入重型新框架（no langgraph-platform/celery/redis）；并发只用 asyncio，持久化只用 sqlite3。

## 5. 风险与未决问题

| 风险 | 应对 |
|---|---|
| GLM TPM 限流在扇出下复发 | Semaphore(4) + max_retries=1 快速失败已有；job log 记 429 则降 Semaphore |
| bigmodel key 未开通 glm-4.6v / MCP 权限 | 阶段 6 第一步先 curl 验证，不通则报告用户 |
| MCP server 版本漂移（npx 缓存旧版） | 固定 @latest + 评测脚本里打印 server 版本 |
| langfuse spans 未落库（HANDOFF 遗留问题3） | 不在本方案范围，但新增 span 时留意勿依赖它调试 |
| 双跑相似度匹配的假阳性/假阴性 | n-gram 阈值 0.6 是初始值，阶段 4 完成后用真实数据人工校准 |
