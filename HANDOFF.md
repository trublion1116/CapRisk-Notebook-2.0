# HANDOFF 交接文档（2026-09-03 会话结束）

> 写给零上下文的新会话。本文包含：项目背景速览、**本会话的两项决策**（MCP 封装方案——已定稿待实施；ON 前端替换评估——结论是暂缓）、遗留问题清单、服务清单与踩坑。
> **新会话的首要任务：按第三节实施 MCP server 封装**，方案已定稿，直接执行无需重新分析。

## 一、项目背景速览

**资金风险 AI 分析系统**（GitHub: trublion1116/CapRisk-Notebook-2.0），分析宏观经济权威报告（BIS 年度报告等）：

- `open-notebook/` — 主项目，fork 自 lfnovo/open-notebook **v1.14.0**，改动方式是把改过的 Python 文件挂载进官方镜像（见 `open-notebook/docker-compose.override.yml`，共 8 处挂载）
- `agent/` — 独立 FastAPI 服务（:5060，deepagents 0.7.11 编排，uv 管理），承担核心观点提取和 chat 代理
- `langfuse/` — 可观测性平台（:3000）

agent 服务架构（本会话已完成全面梳理，直接引用）：

| 层 | 位置 | 说明 |
|---|---|---|
| Chat agent | `agent/app/agents.py:76` `build_chat_agent` | 模块级单例，工具 `list_sources` + `start_core_viewpoint_extraction`（同步跑 5-15 分钟，`get_stream_writer()` 透传细节） |
| Orchestrator | `agent/app/agents.py:157` | 每次提取重建，只持 `submit_insight`，经 `task` 派发子代理 |
| SubAgent 注册表 | `agent/app/subagents/base.py` | `__init_subclass__` 自动注册，`to_spec()` 渲染 deepagents 规格；目前仅 `viewpoint-extraction` 一个 |
| 数据访问 | `agent/app/on_client.py` | **对 ON 的全部依赖**（4 个方法：list/get/download source、create_insight）；注意 `POST /api/insights` 是 ON fork 自建端点 |
| 流式 | `agent/app/chat_stream.py` | `astream(messages+updates+custom)` → SSE，6 种事件 |

REST 接口：`POST /extract {source_id, insight_type}`、`POST /chat/stream {thread_id, system_prompt, messages}`、`GET /jobs/{job_id}`。

端到端链路（已验证）：chat 说"提取核心观点" → list_sources → 同步提取 → 7 批 task 派发 + submit_insight 逐条写回 ON → 前端工具卡片实时展示。

## 二、本会话决策记录

### 决策 1：MCP 封装——方向 B（把 agent 服务能力封装为 MCP server），已定稿待实施

用户在"接入外部 MCP 工具（方向A）"与"封装为 MCP server（方向B）"中**明确选择 B**：用官方 `mcp` python SDK（FastMCP）把现有能力暴露给 Claude Desktop / Cursor 等客户端。

**关键事实**（新会话必读，避免重新踩坑）：
- deepagents 0.7.11 **无原生 MCP 支持**（包内零 mcp 引用）——但 MCP 封装不经过 deepagents，直接包服务层
- `langchain-mcp-adapters` / `mcp` SDK 均**未安装**，需 `uv add mcp`
- 提取任务跑 5-15 分钟，**不能做成同步阻塞的 MCP 工具**（客户端会超时）——必须拆成 start + poll 两工具，复用现有 job 机制（`agent/app/jobs.py`，进程内存 dict）
- `agents.py:121` 已有 `get_stream_writer()` 的 RuntimeError fallback，MCP 进程内直接跑提取不会崩
- chat agent 的单例在 MCP 进程内独立于 FastAPI 进程，无共享冲突

### 决策 2：ON 前端替换——完成评估，**暂缓实施**

用户明确选择"先评估不实施"，且**不做** ContentStore 防腐层改造（"以后再说"）。评估结论存档如下，供未来翻案时使用：

- **换前端不需要动 agent 服务**——它对调用方无感知，source_id 只是不透明字符串，agent 不解析其结构
- 真正成本是重建 ON 产品壳（前端 fork 仅 ~400 行核心，寄生在 ON 完整前端数百组件之上）；最大成本变量 = 实际用到 ON 多少功能
- 最大架构归属问题：chat 的 `system_prompt`（笔记本上下文）由 **ON Python graph 侧**构建（`build_notebook_context` + token 截断），换前端绕过 ON graph 就没人建 context
- 提取流水线完全存储无关（自下载全文、自分节），ON 的 embedding/RAG 在提取链路未被使用
- 两阶段路径：阶段 1（换 UI 留 ON 后端，agent 零改动）成本主要在新前端复刻 SSE 消费 + 工具卡片 + 见解刷新；阶段 2（去 ON）需抽象 on_client + 迁移 context builder + 自建存储
- **建议暂缓**：合理触发条件是功能收敛到"上传→提取→看见解→对话"最小闭环、且不再需要 ON notebook 生态之时

## 三、MCP server 实施方案（定稿，新会话直接执行）

### 文件规划

```
agent/app/mcp_server.py        # 新增：FastMCP 实例 + 4 个工具 + stdio 入口
agent/tests/test_mcp_server.py # 新增：in-memory session 工具测试
agent/Makefile                 # 修改：加 mcp target
```

`.env.example` 不动（无新配置）。**直接 import 复用 `app/` 内部代码，不走 HTTP 自调用。**

### 工具设计（4 个）

| MCP 工具 | 实现 | 要点 |
|---|---|---|
| `list_sources` | `OpenNotebookClient.list_sources()` | 格式同 `agents.py:90` 现有工具（id/标题/类型/见解数），中文 docstring 随现有风格 |
| `start_viewpoint_extraction(source_id, insight_type="核心观点")` | `create_job` + `asyncio.create_task(run_extraction_job(job))` | **立即返回 job_id，不阻塞**——与 chat 版同步等待不同，MCP 无 SSE 透传通道 |
| `get_extraction_job(job_id)` | `get_job()` | 返回 status/error/log/trace_url/sections/时间戳，供轮询；参考 `main.py:206` job_status 的返回结构 |
| `ask_agent(message)` | `build_chat_agent()` 懒加载单例 + `ainvoke` 非流式 | 取最后一条 AIMessage 文本，复用 `chat_stream.py:29` 的 `text_of`；工具描述里引导长提取走 start+poll 而非对话触发 |

### stdio 入口与运行

- `mcp_server.py` 末尾 `if __name__ == "__main__": mcp.run()`（stdio 传输）
- **必须加载 .env**（提取/问答都需要 LLM 配置）：Makefile 加 `mcp:` target：`uv run --env-file .env python -m app.mcp_server`；或在 `__main__` 里 `load_dotenv` 兜底（注意 Makefile dev 用的是 uvicorn --env-file 方式）
- Windows 侧客户端（Claude Desktop 装在 Windows）配置需 wsl.exe 包装，交付说明给配置片段示例

### 依赖与验证

- `uv add mcp`（python SDK，与 langchain 1.x 无冲突，Python 3.11 OK）
- 测试用 `mcp` SDK 的 `ClientSession` + memory transport 直接调工具：mock `OpenNotebookClient`（参考现有 `tests/test_chat_tools.py` 的做法）；断言 start 立即返回 job_id、get_extraction_job 字段完整、list_sources 格式
- 完成后 `make lint && make test`
- 注意 WSL /mnt/c 下 inotify 失效：改代码必须重启服务才生效（见踩坑）

### 已知取舍（接受，不做额外处理）

- job 表在各进程内存独立：MCP 进程创建的 job 查不到 FastAPI 的 `/jobs/{id}`，反之亦然；见解写回 ON 两边一致，不影响数据
- 阶段 2（可选，本次不做）：`mcp.streamable_http_app()` 挂载进 FastAPI 同端口 + AGENT_API_KEY 认证，好处是 job 表与主服务共享。用户未确认要做

## 四、遗留问题（上一会话遗留，均未解决）

1. job `cb578c732532` 失败根因未明（error 空串，来源已删无法复现）。诊断版已部署，下次失败看 `/tmp/opencode/agent.log` 的 traceback
2. langfuse spans 未落库（trace 创建成功但 observations 为空）——`app/tracing.py` 的 handler/flush 机制未排查
3. `98339f9` 两个前端修复**用户尚未验证**：提取中切窗口不丢内容、见解列表随 submit_insight 逐条刷新
4. 前端新功能只在 3001 dev 可用（8502 是镜像内旧 production）
5. agent job 表进程内存，重启丢任务（todo.md 已知问题）

## 五、服务清单

| 服务 | 启动方式 | 地址 | 状态 |
|---|---|---|---|
| ON（API+worker+旧前端） | `cd open-notebook && docker compose up -d --pull never` | 5055 / 8502 | ✅ |
| agent（deepagents） | `scripts/agent-start.sh`（日志 `/tmp/opencode/agent.log`）；交互式 `cd agent && make dev` | 5060 | ✅ |
| 前端 dev（新功能） | `cd open-notebook/frontend && npm run dev -- -p 3001` | 3001 | ✅ |
| langfuse | `cd langfuse && docker compose up -d` | 3000 | ✅ |
| SurrealDB | 随 ON compose，数据在 `/home/wangj/onv2_surreal_data`（勿改回 /mnt/c） | 容器内 8000 | ✅ |

## 六、踩坑速查（新会话必读）

### 部署
- compose 服务名是 **`open_notebook`**（下划线）；启动命令必须带 **`--pull never`**（Docker Hub 连不上）
- 旧容器丢 Docker 网络挂载 → `--force-recreate` 重建
- SurrealDB rocksdb 不能放 /mnt/c（Windows 9p 挂载导致认证失败）
- **WSL 闪退 / Docker Desktop 重启后，自动恢复的容器会丢 bind mount**（2026-09-12 实测：surrealdb 挂载变成空 named volume，ON 表现为"所有数据消失"假象）。处置：`docker compose up -d --pull never --force-recreate surrealdb` 按 compose 配置重建即恢复，**不要急着重灌数据**；先 `docker inspect <容器> --format '{{json .Mounts}}'` 核对 Source 是否还指向 /home/wangj/onv2_surreal_data
- **surrealdb 重建后必须重启 open_notebook 容器**：POST /api/insights 是 fire-and-forget 异步命令，ON 容器若还持有旧库连接，worker 会停止消费命令队列（API 日志见 "Submitted create_insight command" 但见解不落库）。处置：`docker compose restart open_notebook`，worker 重扫队列即补齐

### WSL /mnt/c 的 inotify 失效（影响最大）
- Turbopack 热重载、uvicorn --reload 均检测不到文件变化 → **改代码必须重启服务 + 浏览器硬刷新**

### 前端运行环境（2026-09-12 新坑）
- **node_modules 是 Windows 侧装的**（含 win32 二进制）→ WSL 里 `next dev` 报 `Cannot find module '../lightningcss.linux-x64-gnu.node'` / swc 缺失。已补装：`npm install --no-save @next/swc-linux-x64-gnu lightningcss-linux-x64-gnu @tailwindcss/oxide-linux-x64-gnu`
- Turbopack 在此环境不可用（装完 linux swc 后默认 dev 可跑；若仍崩退 `next dev --webpack`）
- 3001 端口被僵尸 next 进程占用时（EADDRINUSE）：`for p in $(pgrep -f next); do kill -9 $p; done` 后重启
- **浏览器(Windows)直连 WSL 内 uvicorn(5060) 的 localhost 转发不可靠** → 见解报告里的图表图片**必须用相对路径 `/agent-images/{source_id}/{name}`**，由 next.config.ts 的 rewrite 同源代理到 5060（已配置，勿删）；agent 侧 `AGENT_PUBLIC_URL` 默认留空即生成相对路径，仅在无代理场景才配绝对 URL

### 流式与前端
- Next dev(3001) 的 `/api` rewrite 会**缓冲 SSE** → 所有流式请求必须直连 5055（`getApiUrl()`），勿改回相对路径
- React Query focus refetch 会覆盖流式气泡 → messages 同步 effect 的 `isStreaming/isSending` gate 注意保持
- i18n 14 个 locale 必须同步加 key（parity 测试强制）

### agent
- `agents.py ↔ runner.py` 循环导入：runner 在 `build_chat_agent` 内延迟导入
- pkill 模式匹配 shell 自身会挂起：用 `for p in $(pgrep -f ...); do kill -9 $p; done` 并排除 $$
- 异常记录用 `repr(e)`（TimeoutError 的 str 为空串）
- deepagents 的 async 工具内 `get_stream_writer()` 可用，事件经 astream custom 模式冒泡——嵌套编排细节透传的关键机制

## 七、关键链路速查

```
见解提取（同步流式）:
chat "提取核心观点" → /chat/execute/stream (SSE)
  → ON graph 节点 _stream_agent_service (httpx 流式 + writer)
    → agent /chat/stream (chat agent)
      → 工具 start_core_viewpoint_extraction（同步执行）
        → run_extraction_job(on_event) → 编排 agent astream(updates)
          → task×7批 / submit_insight×N 事件
            → custom 流 → SSE → 前端 ToolCallTray 卡片

事件类型: token | tool_call | tool_result | final | complete | error
job 查询: GET :5060/jobs/{job_id}；见解写回: POST :5055/api/insights
```
