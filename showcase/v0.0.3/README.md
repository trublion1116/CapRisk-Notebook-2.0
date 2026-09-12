# v0.0.3 — 章节结构化 + 图表分析 + Box 专题（2026-09-12）

## 本版效果

对 BIS Annual Economic Report 2026（完整版 133 页）提取，产出**一篇结构化报告**
（`insights.md`，约 5 万字符）：

```
# 《报告》核心观点报告
## 总览
## 第一章 / 第二章 / 第三章（观点：原文引用 + 解读 + 新颖性/重要性）
### Box A/B/...（章内专题框，完整论点链条）×10
```

- 16 张图表页渲染图（200DPI PNG）嵌入相关观点处，经前端同源代理显示
- job 用时 ~11 分钟（ingest 5s + VLM 图表描述 43/45 张 ~7.5min + 编排合成 ~3min）

## 技术方案（当前架构）

```
ON 摄取（pdfplumber, on-parsed full_text 备用）
        ↓ download API 取原始 PDF（PDF 优先）
agent 数据面 app/ingestion/（纯代码策略链，pkgutil 自动注册）
  outline（书签：章/节/Box 层级，83 节/4 章/10 box）   ← 主策略，BIS 书签完整
  → markdown_header（docling 产物适配）
  → heading_regex（无书签 PDF 正则兜底）
  → char_fallback（旧 split_sections 迁移）
  images.py：矢量密度判图表页（paths≥60，实测纯文本 ~12 vs 图表页 145+）
             → pypdfium2 整页 200DPI 渲染 → 按页归属挂 section
        ↓
VLM 图表描述 app/vision.py（bigmodel glm-4.6v，独立 VISION_* 配置）
  thinking 关闭（34s→20s/张），结构化输出（图号/坐标/图例/数值/趋势）
  失败降级不阻塞；并发 2；进度以 chart_reader 事件透传前端
        ↓
编排 deepagent（文本 LLM：deepseek-v4-flash）
  task 区间派发（15 节/批）→ viewpoint-extraction worker 全覆盖阅读
  （候选带章节 + 相关图表标注）→ 合成一篇结构化报告
  → submit_report 单次提交（ON insight）
  → list_charts 工具给编排图表清单（markdown 引用，相对路径 /agent-images/...）
        ↓
ON 前端(3001 dev)：next.config rewrite /agent-images/* → :5060/images/*
（同源代理，规避 Windows→WSL localhost 转发不可靠）
```

## 启动方式（顺序）

```bash
# 1. ON（docker；--pull never 必带，服务名 open_notebook 下划线）
cd open-notebook && docker compose up -d --pull never
# Docker Desktop 重启过 → 容器可能丢 bind mount/网络，数据"消失"先 force-recreate：
#   docker compose up -d --pull never --force-recreate surrealdb && docker compose restart open_notebook

# 2. agent（:5060；改代码必须重启，WSL inotify 失效）
bash scripts/agent-start.sh        # 日志 /tmp/opencode/agent.log

# 3. 前端 dev（:3001；node_modules 若缺 linux 二进制见 HANDOFF 踩坑）
cd open-notebook/frontend && npx next dev -p 3001

# 4. 触发提取
curl -X POST http://localhost:5060/extract -H 'Content-Type: application/json' \
  -d '{"source_id":"source:xxx","insight_type":"核心观点"}'
# 或 chat 里说"提取核心观点"（SSE 实时展示编排细节）
```

## 关键配置（agent/.env）

- `LLM_*`：文本主模型（当前 deepseek-v4-flash）
- `VISION_*`：图表 VLM（bigmodel glm-4.6v，独立于文本模型）
- `AGENT_PUBLIC_URL`：默认空 = 图片相对路径走前端代理；仅无代理场景配绝对 URL

## 已知限制（下一步优化方向）

- 图表图片是**整页截图**，未裁剪单个图表区域 → 计划按 pdfium 对象 bbox 聚类裁剪
- 总览为观点罗列，缺"结构化总览 + reference（观点→原文页码/章节索引）"
- quote 未做逐字校验、无 gold 评分基准（V2 阶段 1 暂缓，用户决定）
- 章节标注由 LLM 自填，偶有错标（实测 15 条中 1 条）
