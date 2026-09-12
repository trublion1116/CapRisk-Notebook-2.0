# HANDOFF 交接文档（2026-09-02 会话结束）

> 写给零上下文的新会话。配套的《启动指南.md》有更详细的部署细节，本文侧重交代来龙去脉和当前状态。

## 一、项目背景与任务目标

**资金风险 AI 分析系统**（GitHub: trublion1116/CapRisk-Notebook-2.0），用于分析宏观经济权威报告（BIS 年度经济报告等）：

- `open-notebook/` — 主项目，fork 自 lfnovo/open-notebook **v1.14.0**，改动方式是**把改过的 Python 文件挂载进官方镜像**（见 `open-notebook/docker-compose.override.yml`），不重建镜像
- `agent/` — 独立 FastAPI 服务（deepagents 编排框架，uv 管理），承担核心观点提取和 chat 代理，端口 5060
- `langfuse/` — Langfuse 可观测性平台副本（docker compose 跑，端口 3000）

本会话的任务演进：跑通部署 → 见解生成接入外部 agent（双通道触发）→ chat 流式化展示工具调用细节（方案B）→ 提取改同步流式 → 一系列前端渲染 bug 修复。

## 二、已完成内容（按 git 提交顺序）

| 提交 | 内容 |
|---|---|
| `0dc31db` | 启动指南.md（部署命令 + 踩坑） |
| `77d87a3` | **见解生成双通道触发**：① 界面"生成新见解"下拉框的 `agent_extract` 伪转换规则（`api/routers/sources.py` 识别后转调 agent `/extract`，seed 脚本 `scripts/seed_agent_transformation.py` 幂等创建）；② AI 助手意图识别——chat agent 加 `list_sources` / `start_core_viewpoint_extraction` 工具 |
| `1e33c99` | **chat SSE 流式（方案B）**：agent `/chat/stream`（deepagents astream messages+updates+custom）→ ON graph 节点（httpx 流式 + `get_stream_writer` 透传）→ ON `POST /chat/execute/stream`（queue+to_thread 桥接同步 graph.stream）→ 前端 fetch SSE 消费。notebook chat 先接 |
| `c8dd24a` | source chat 接入同一套流式协议（`api/routers/source_chat.py` 真流式改造，保留旧事件兼容） |
| `e75130b` | 修 dev rewrite 缓冲 SSE（source chat 直连 5055）+ **toolCalls 持久化**（graph 节点写入 `AIMessage.additional_kwargs` → checkpoint → `_chat_shared.py` 透传 → 刷新页面工具卡片仍在） |
| `4189a6f` | **提取改同步流式**：`start_core_viewpoint_extraction` 同步执行整个流水线（5-15 分钟），编排细节（7 批 task 派发、逐条 submit_insight）通过 custom 流实时透传到工具卡片；`asyncio.shield` 保证浏览器断开后提取后台跑完；AGENT_TIMEOUT 600→1800 |
| `094311d` | 失败可见化：`job.error = repr(e)` + `logger.exception`（此前 `asyncio.TimeoutError` 等 str 为空导致根因完全不可见）；`scripts/agent-start.sh` 统一 agent 启动 |
| `98339f9` | 前端修复：messages 同步 effect 加 streaming gate（切窗口 focus refetch 覆盖流式气泡）+ finally 时序修正 + `onv2:insights-updated` 事件驱动见解列表实时刷新（submit_insight 每落一条刷一次） |

**端到端已验证**（ar2026e-cut.pdf，11 节）：chat 说"提取核心观点" → `list_sources` → 同步提取 → task 卡片 → 15 条 submit_insight 逐条实时出现 → 最终汇报 → 16 条见解入库。最后一条提交 `98339f9` 的两个修复**用户尚未验证**。

## 三、当前问题（未解决）

1. **job `cb578c732532` 失败根因未明**（18:04 启动，100 秒后失败，error 空串）。用户删了来源无法复现。已部署诊断版（repr+traceback），下次失败日志会出现在 agent 日志（`/tmp/opencode/agent.log`）。嫌疑：空消息异常（TimeoutError 类）或 shield 版本引入的取消传播；18:06 前后 ON 日志还有一条 "peer closed connection (incomplete chunked read)" 未解释
2. **langfuse spans 未落库**：trace 创建成功但 `/api/public/observations?traceId=...` 返回空——langchain langfuse handler 异步批量导出未生效/被吞，未排查
3. **`98339f9` 的修复待验证**：切窗口不丢内容、见解逐条实时刷新（用户被要求验证前就结束了会话）
4. **前端新功能只在 3001 dev 可用**：8502 是镜像内旧 production。长期需要重建镜像或宿主机 build 挂载（standalone 产物结构，未做）
5. agent 的 job 表在**进程内存**（重启即丢，正在跑的任务也会被杀）——todo.md 既有已知问题

## 四、下一步计划

1. 让用户在 3001 硬刷新（Ctrl+Shift+R）后验证 `98339f9`：提取过程中切走窗口再回来内容不丢；左边见解列表随 submit_insight 逐条增长
2. 等下一次提取失败，从 `/tmp/opencode/agent.log` 的 traceback 定位根因（若再遇到 "peer closed connection"，查 ON→agent 的 httpx 流在 uvicorn 层的断连）
3. 排查 langfuse 导出（`app/tracing.py` 的 handler/flush 机制）
4. 可选方向（todo.md 有记录）：前端 production 化、agent job 持久化（SurrealDB/SQLite）、把"向知识库提问(ask)"也接 agent 服务、见解可读性优化、agent 触发去 curl 化（已解决大半）

## 五、服务清单（当前运行状态）

| 服务 | 启动方式 | 地址 | 状态 |
|---|---|---|---|
| ON（API+worker+旧前端） | `cd open-notebook && docker compose up -d --pull never` | 5055 / 8502 | ✅ 在跑 |
| agent（deepagents） | `scripts/agent-start.sh`（setsid nohup，日志 `/tmp/opencode/agent.log`）；交互式用 `cd agent && make dev` | 5060 | ✅ 在跑（诊断版） |
| 前端 dev（新功能都在这） | `cd open-notebook/frontend && npm run dev -- -p 3001` | **3001** | ✅ 在跑 |
| langfuse | `cd langfuse && docker compose up -d`（已在跑） | 3000 | ✅ |
| SurrealDB | 随 ON compose，数据在 `/home/wangj/onv2_surreal_data` | 容器内 8000 | ✅ |

依赖环境：agent 的 `agent/.env`（LLM_API_KEY/LLM_BASE_URL=bigmodel、ON_API_TOKEN、LANGFUSE key）；ON 的 `open-notebook/.env`（OPEN_NOTEBOOK_ENCRYPTION_KEY）。

## 六、踩过的坑（新会话必读）

### 部署
- compose 服务名是 **`open_notebook`**（下划线），不带服务名则全部启动
- 主 compose `pull_policy: always` + Docker Hub 连不上 → 启动命令**必须带 `--pull never`**
- 旧容器会丢失 Docker 网络挂载（inspect NetworkSettings.Networks 为空，应用报 `Name or service not known`）→ `--force-recreate` 重建
- SurrealDB 的 rocksdb **不能放 /mnt/c**（Windows 9p 挂载导致认证失败），override 已指向 WSL 原生路径，勿改回
- 3000 被 langfuse 占用，前端 dev 用 **3001**

### WSL /mnt/c 的 inotify 失效（影响最大，反复咬人）
- **Turbopack 热重载无效**：改前端后必须重启 dev server + 浏览器硬刷新，否则浏览器跑旧代码、"修复不生效"假象
- **uvicorn --reload 同样检测不到**：改 agent 代码后必须重启 agent 服务
- 完整重启命令见《启动指南.md》

### Next dev 与 production 行为不一致
- dev(3001) 的 `/api` rewrite 会**缓冲 SSE 响应**（一次性 flush，流式效果全无；8502 的 server.js rewrite 正常）→ **所有流式请求必须直连 5055**（`getApiUrl()`），前端 `chat.ts` / `source-chat.ts` 已这样处理，勿改回相对路径

### 前端
- React Query 的 **focus refetch** 会在提取长跑期间用旧会话历史覆盖流式气泡 → messages 同步 effect 已加 `isStreaming/isSending` gate，注意保持
- i18n 14 个 locale 必须同步加 key（`satisfies TranslationShape` + parity 测试强制）；本会话加了 `chat.toolCallArgs/toolCallResult`
- 前端检查命令：`npm run lint` / `npm run test` / `npx tsc --noEmit`（宿主机 WSL 已 npm ci，750 包）

### agent
- `run_extraction_job` 的异常 `str(e)` 可能为空（TimeoutError 等）→ 用 `repr(e)` + `logger.exception`（已修）
- `agents.py ↔ runner.py` 循环导入：runner 在 `build_chat_agent` 内**延迟导入**
- pkill 模式会匹配 shell 自身导致命令挂起：用 `for p in $(pgrep -f ...); do kill -9 $p; done` 并排除 $$
- deepagents 的 async 工具内 `get_stream_writer()` 可用（已实验验证），事件经 astream custom 模式冒泡——这是嵌套编排细节透传的关键机制
- agent 重启会丢正在跑的提取任务（内存 job 表 + BackgroundTasks）

## 七、关键链路速查

```
见解提取（同步流式）:
chat "提取核心观点" → /chat/execute/stream 或 /sources/{id}/chat/.../messages (SSE)
  → ON graph 节点 _stream_agent_service (httpx 流式 + writer)
    → agent /chat/stream (chat agent)
      → 工具 start_core_viewpoint_extraction（同步执行）
        → run_extraction_job(on_event) → 编排 agent astream(updates)
          → task×7批 / submit_insight×N 事件
            → custom 流 → SSE → 前端 ToolCallTray 卡片

事件类型: token | tool_call | tool_result | final | complete | error
（source chat 另有兼容事件 ai_message / context_indicators / user_message）
job 查询: GET :5060/jobs/{job_id}；见解写回: POST :5055/api/insights
```
