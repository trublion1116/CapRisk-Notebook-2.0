# HANDOFF 交接文档（2026-09-12 会话二：可观测性进行中，代码未提交）

> 写给零上下文的新会话。配套：`AGENT_V2_PLAN.md`（六阶段升级方案）、`AGENT_CLUSTER_RESEARCH.md`（框架选型）、`showcase/vX.Y.Z/`（各版本实际效果快照）。
> **当前状态**：v0.0.5 已推送（tag 对齐）；本会话在做"全链路可观测性"（用户想看到图表提取/图表分析/观点提取/报告生成各阶段的执行者与过程）——**阶段 B/C 代码已完成但未提交**，阶段 D 验证跑到一半被暂停，langfuse 服务端修复被网络阻塞。详见"下一步计划"第 0 条。

## 一、项目背景速览

**资金风险 AI 分析系统**（GitHub: trublion1116/CapRisk-Notebook-2.0），分析 BIS 年度经济报告等宏观报告：

- `open-notebook/` — 主项目，fork 自 lfnovo/open-notebook **v1.14.0**（= 上游最新版，调研确认无需升级），改动方式为挂载 Python 文件进官方镜像（`docker-compose.override.yml`，10 个后端文件）+ 前端 dev 版改动（28 文件，未挂载进容器）
- `agent/` — FastAPI 服务（deepagents + uv，:5060）——**提取流水线与 chat 代理**
- `langfuse/` :3000 | 前端 dev :3001 | ON :5055/8502 | SurrealDB（bind mount 在 `/home/wangj/onv2_surreal_data`）

## 二、当前提取流水线（v0.0.5 架构）

```
chat"提取核心观点" → start_core_viewpoint_extraction → run_extraction_job
层0 数据面（纯代码）:
  fetch PDF 优先（书签/图表只有 PDF 有）→ app/ingestion/ 策略链
  outline（书签，BIS 83节/4章/10Box 全识别）→ markdown_header → heading_regex → char_fallback
  images.py: 矢量密度判页（paths≥60）→ bbox 空间聚类裁剪单图表区域
    （贯穿线过滤 + 超45%簇逐级细分 + 图注文本定位双向并入，B1 69%→25%）
  → pypdfium2 200DPI 渲染 PNG（agent/data/images/{sid}/，page_NNN_M.png）
VLM 图表描述 app/vision.py + subagents/chart_reader.py: bigmodel glm-4.6v（VISION_* 独立配置，thinking off 20s/张）
  → notes.json 磁盘缓存（NOTES_CACHE_VERSION 版本失效，重跑秒级命中）
全链路结构化日志（agent.log）: [stage]/[chart-reader]/[subagent] 前缀可 grep，
  四阶段执行者归因：fetch=runner / ingest=ingestion·images.py / chart-describe=chart-reader /
  观点提取=viewpoint-extraction（task 派发计时）/ 报告生成=orchestrator（report-submit）
编排 deepagent（文本 LLM deepseek-v4-flash）:
  task 区间派发（15节/批）→ viewpoint-extraction worker 全覆盖阅读
  （候选带出处页码+相关图表）→ 合成一篇结构化报告 → submit_report 单次提交
  （落库前清洗 URL 前导不可见空白 U+2009）
报告格式（F1-F7 硬性 prompt 指令）:
  总述段 → 一、章节核心观点（加粗要点+角标）→ 二、Box 深度提炼（按章分组）
  → 三、政策与市场含义 → References（66条：出处/页码/图表/逐字英文摘录）
前端（v0.0.5）:
  角标 [n](#ref-n) → SourceRefLink（hover 出处+原文 / 点击跳 source 详情）
  → hl 参数定位卡 + 全文段落 scrollIntoView+琥珀闪烁（locateTextInDom 两级容错：
  精确子串→空白/重复字符归一化，pdfplumber "AAnnnnuuaall" 重复字符实测可定位）
  图片 /agent-images/* 经 next.config rewrite 同源代理到 :5060
  见解"保存到笔记"（ON 原生 POST /insights/{id}/save-as-note，目标=来源所属笔记本）
```

实测数据（ar2026e 完整版 133 页）：ingest 2s → VLM 46张 ~7.3min（缓存命中则秒级）→ 编排 ~4min；报告 ~4.2 万字符 / 134 角标 / 15 裁剪图。

## 三、本会话提交记录（均已推送，tag 对应 showcase）

| tag | commit | 内容 |
|---|---|---|
| v0.0.3 | e3324c3 | ingestion 策略链+Box剥离+VLM图表+单篇报告+同源图片代理+Clash TUN proxy 修复 |
| v0.0.4 | 6175c39→e0af073 | 总览表+References+[PN]编号；图表精准裁剪（贯穿线过滤/超限细分/图注并入）；VLM 缓存；U+2009 图裂修复 |
| v0.0.5 | 720ba46→dea2a90 | 报告深度结构化（对齐人工研报样例）+引用角标系统+保存到笔记+滚动定位高亮 |
| — | 62f4319/85710aa | showcase 图片自包含（GitHub 渲染）+ v0.0.5 角标转 GFM 脚注语法 |

## 四、下一步计划（用户已确认方向）

0. **【进行中】全链路可观测性**（本会话任务，断点续接处）：
   - 已完成：①`app/subagents/chart_reader.py`——图表解读归一为注册 subagent（SYSTEM_PROMPT 引用 vision.CHART_PROMPT，build_tools 空 + description 注明编排勿派发，实际调度保持代码循环）②`runner.py` 结构化阶段日志：`[stage] name=fetch/ingest/chart-describe/orchestrate/report-submit agent=... took=...`、`[chart-reader] image=... cache=miss chars=... took=...`、`[subagent] dispatch/done agent=viewpoint-extraction`；编排统一 astream（/extract 无 SSE 也产生日志）；测试 39 passed + ruff 干净。**以上均未 commit**
   - **待办 a（先做）**：跑迷你端到端验证日志——`cd agent && PYTHONPATH=. timeout 900 uv run python /tmp/opencode/mini_e2e_obs.py`（脚本在 /tmp，丢了就按 git 里 test_runner_events 的 mock 方式重写），确认 `[subagent]` 与 `[chart-reader]` 日志行出现，然后 **commit 这批代码**
   - **待办 b（被网络阻塞）**：langfuse 服务端升级修复（根因见遗留问题 2）——需 Docker Desktop 配 Clash 代理（Settings→Resources→Proxies→`http://host.docker.internal:7897`，注意 WSL shell 的 proxy_on 不影响 Windows 侧 daemon）且 Clash 节点放行 Docker Hub，然后 `cd langfuse && docker compose pull && docker compose up -d`（会自动跑 ClickHouse 迁移）。升级后补 span 埋点：VLM 调用挂 `new_handler()` callbacks、task 派发生命周期开 child_span（日志版逻辑已就位，加 span 是平行小改）
1. **桌面演练/推演（样式后置）**——用户已确认四个决策：
   - 触发：chat 触发（"推演：霍尔木兹封锁再持续 3 个月"）+ 推演报告作为新见解入库（复用 SSE/角标/保存笔记链路）
   - 论据：已提取的结构化报告（观点+图表数据），引用可回溯（复用角标系统）
   - 集群形态：多 agent 并行推演（冲击传导/资金面/政策反应三视角）→ 对抗式汇总（多空互补）→ 交叉审查成稿
   - 设计上可复用：ingestion/引用管线/报告格式 F1-F7/worker 注册表（新增推演子代理=新模块文件）
2. **评估 agent**（用户原话措辞已定稿）：以专家分析版本为 gold standard，自动比对观点提取产出与专家结论差距（recall/precision），量化评分驱动提取 agent 的 prompt 与策略迭代——即 V2 计划阶段 1c 的 agent 化
3. **样式优化 6 项清单**（演练后统一处理）：①见解列表预览裸露 markdown 语法 ②References 长表格窄容器拥挤 ③图片无尺寸约束/lightbox ④标题四级过密 ⑤定位卡与段落闪烁两套高亮可弱化其一 ⑥dark 模式 mark/表格对比度
4. 既有暂缓项：MCP server 封装（方案定稿见本文件第七节）、ON 前端替换（评估结论见第八节）

## 五、遗留问题

1. References 页码列偶为 "—"（本轮编排未从候选带出页码；跳转定位靠原文摘录不受影响）——可在 F6 加页码强制校验
2. **langfuse spans 落库问题——根因已完全定案（2026-09-12 排查链）**，非 agent 侧问题：
   - agent→langfuse 链路全通：SDK 4.15.1 auth_check ✓；OTLP 正确端点是 **`/api/public/otel/v1/traces`（是 `otel` 不是 `otlp`**，别再 curl 错路径）；curl 直发 200 入队；worker 消费正常（minio `events/` 13 天 343MB、ClickHouse **events_core 11671 行实时更新**，含 SPAN/CHAIN/AGENT/GENERATION/TOOL）
   - **真断点**：`traces`/`observations`/`analytics_*` 读优化表全 0——v4.0 早期镜像（13 天前拉的 `:4` 滚动 tag）的 trace-upsert 派生缺陷（redis 里 `bull:trace-upsert` 队列存在但从未有 job 投递，上游 web 不投）
   - 修复 = 升级镜像；但 `docker.langfuse.com` 307 重定向到 registry-1.docker.io（被墙），Clash 节点对 Docker Hub 也 000 → 待用户配 Docker Desktop 代理 + 可用节点
   - **坑**：`LANGFUSE_S3_BATCH_EXPORT_ENABLED` 是"数据导出到外部 S3"功能，**与 ingestion 无关**——误开会每调度周期报 ioredis socket timeout（已撤销）。另 worker 有偶发 Redis 30s 超时（多队列同时报），疑 WSL Docker 网络抖动，未处理
3. agent job 表进程内存（重启丢正在跑的任务）；V2 阶段 1b 的 SQLite 持久化未做（用户暂缓了阶段 1）
4. quote 逐字校验 + gold 评分未做（用户明确暂缓，改由"评估 agent"方向承接）
5. locale unused-key 测试在 WSL /mnt/c 上环境性超时（30s 扫不完源码树，非功能问题）
6. 前端 production 化未做（新功能只在 3001 dev）

## 六、踩坑速查（新会话必读）

### 网络（2026-09-11 大坑，先读这条）
- **当前 WSL 是 Clash TUN 模式（fake-ip DNS）**：LLM 域名解析到 198.18.x.x，绕过 proxy 直连必挂。`config.py` 默认走系统 proxy（正确），`LLM_BYPASS_PROXY=1` 是逃生开关（仅非 TUN 环境用）
- **浏览器(Windows)直连 WSL 内 uvicorn(5060) 的 localhost 转发不可靠** → 报告图片必须用相对路径 `/agent-images/{sid}/{name}`，由 next.config.ts rewrite 同源代理（勿删）；agent 侧 `AGENT_PUBLIC_URL` 默认留空

### 部署
- compose 服务名 `open_notebook`（下划线）；启动必须带 `--pull never`
- 旧容器丢 Docker 网络 → `--force-recreate`
- **WSL 闪退/Docker Desktop 重启后，自动恢复的容器会丢 bind mount**（surrealdb 数据"消失"假象）。先 `docker inspect <容器> --format '{{json .Mounts}}'` 核对，再 `docker compose up -d --pull never --force-recreate surrealdb` 恢复，**不要急着重灌数据**
- **surrealdb 重建后必须 `docker compose restart open_notebook`**：POST /api/insights 是 fire-and-forget 异步命令，旧连接的 worker 停止消费队列（日志见 "Submitted create_insight command" 但不落库），重启即补齐

### WSL /mnt/c 的 inotify 失效
- Turbopack 热重载、uvicorn --reload 均失效 → **改代码必须重启服务 + 浏览器硬刷新**

### 前端运行环境
- **node_modules 是 Windows 侧装的**（win32 二进制）→ WSL dev 报 `Cannot find module '...linux-x64-gnu.node'`。已补装：`npm install --no-save @next/swc-linux-x64-gnu lightningcss-linux-x64-gnu @tailwindcss/oxide-linux-x64-gnu`
- Turbopack 崩时退 `next dev --webpack`；3001 被僵尸 next 占用（EADDRINUSE）：`for p in $(pgrep -f next); do kill -9 $p; done`
- Next dev 的 `/api` rewrite 会缓冲 SSE → 流式请求必须直连 5055（`getApiUrl()`），勿改相对路径
- i18n 15 locales（含 zh-TW）必须同步加 key（parity 测试 + tsc 强制）
- 前端检查：`npm run lint` / `npx tsc --noEmit` / `npx vitest run <file>`

### agent
- LLM 调用超时用 `repr(e)` 记录（TimeoutError 的 str 为空）
- pkill 匹配 shell 自身会挂起：`for p in $(pgrep -f ...); do kill -9 $p; done` 排除 $$
- pypdfium2 5.x 需 `io.BytesIO` 包装（裸 bytes AttributeError）；对象 bbox 用 `get_bounds()`（无 get_pos）
- deepagents 的 async 工具内 `get_stream_writer()` 可用，事件经 astream custom 模式冒泡
- LLM 生成 markdown 会在 `](` 与 URL 间插不可见空白（U+2009 家族）→ submit_report 落库前正则清洗（`_URL_WS_RE`）
- glm-4.6v 默认开 thinking（全页图表 reasoning 170s+）；`extra_body={"thinking":{"type":"disabled"}}` 后 20s/张质量无损——注意 extra_body 必须走 extra_body 参数，顶层透传被 openai SDK 拒绝
- dotenv 的 `load_dotenv()` 按调用者脚本目录找 .env——脚本放 /tmp 时要显式 `load_dotenv(".env")`（cwd 为准）
- **Docker pull 的代理在 Windows 侧 daemon**：WSL shell 里 proxy_on（7897）不影响 docker pull；langfuse 镜像源会重定向 Docker Hub（被墙）

### ON 原生能力结论（2026-09-11 调研）
- 上游 v1.14.0 = 最新版；图片资产/章节实体/insight tags/多模态对话上游全没有，勿等上游
- PDF 引擎：pdfplumber 默认（文本层有重复字符 bug），docling 需显式开（`docling_vision` 可摄取期图表转文字，未启用）
- insight 无 tags/metadata 字段——结构化靠内容 markdown（章节前缀/角标），改上游 schema 不值得

## 七、MCP server 封装方案（定稿未实施，保留备用）

用户已选**方向 B**（封装为 MCP server 供 Claude Desktop/Cursor 用）。要点：`uv add mcp`；FastMCP + 4 工具（list_sources / start_viewpoint_extraction **异步返回 job_id 不阻塞** / get_extraction_job 轮询 / ask_agent 非流式）；stdio 入口 `python -m app.mcp_server`（Makefile 加 `mcp:` target，`--env-file .env`）；job 表各进程内存独立（已接受的取舍）。Windows 客户端需 wsl.exe 包装配置。

## 八、ON 前端替换评估（结论：暂缓）

换前端不需动 agent（source_id 对 agent 不透明）；真实成本 = 重建 ON 产品壳 + chat 的 notebook context 由 ON graph 侧构建（绕不开）；触发条件：功能收敛到"上传→提取→看见解→对话"最小闭环且不再需要 ON notebook 生态之时。

## 九、服务清单

| 服务 | 启动 | 地址 |
|---|---|---|
| ON | `cd open-notebook && docker compose up -d --pull never` | 5055 / 8502 |
| agent | `bash scripts/agent-start.sh`（日志 /tmp/opencode/agent.log） | 5060 |
| 前端 dev | `cd open-notebook/frontend && npx next dev -p 3001` | 3001 |
| langfuse | docker compose（已在跑） | 3000 |

验证素材：BIS AR2026 完整版已上传（`source:6rxddpszvsnganzpbxv8`，133 页，含 v0.0.5 报告见解）；PDF 官方下载 `https://www.bis.org/publications/aer-2026_0.pdf`（ar2026e.pdf 直链已变，从报告页 HTML 里取 publications 链接）。

## 十、关键链路速查

```
见解提取: chat → agent /chat/stream → start_core_viewpoint_extraction（同步）
  → run_extraction_job(on_event) → ingest → VLM 描述 → 编排 astream(updates)
  → task 派发 / submit_report 事件 → custom 流 → SSE → 前端工具卡片
角标跳转: SourceInsightDialog 角标点击 → ?modal=source&hl=<quote>&hl_label=...
  → SourceDetailContent 定位卡 + locateTextInDom 滚动闪烁高亮
保存笔记: 卡片/对话框按钮 → POST :5055/api/insights/{id}/save-as-note
job 查询: GET :5060/jobs/{id}；图表 PNG: GET :5060/images/{sid}/{name}
  （前端经 :3001/agent-images/* 同源代理）
事件类型: token | tool_call | tool_result | final | complete | error
```
