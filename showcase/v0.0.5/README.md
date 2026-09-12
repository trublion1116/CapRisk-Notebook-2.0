# v0.0.5 — 引用角标系统 + 保存到笔记 + 报告深度结构化（2026-09-12）

对比 v0.0.4 的三项升级（`insights.md` 为本版效果）：

## 1. 报告深度结构化（对齐人工研报样例）

```
# 《报告》核心观点报告
（总述段：核心主题 2-4 句）
## 一、报告整体框架与章节核心观点
### 1. 第一章：进展与风险（I. Progress and peril）
* **要点名**：展开说明 [1](#ref-1)   ← 每章 2-4 条加粗要点
## 二、核心 Box（专题研究）深度提炼
### 第N章专题
* **Box A：中文标题（英文原题）**：论点链条/量化结论 [n](#ref-n)
## 三、政策与市场含义（跨章综合）
## References（观点出处索引）
| 编号 | 出处（章/节） | 页码 | 涉及图表 | 原文摘录 |   ← 原文摘录供跳转定位
```

本版实测：26,637 字符 / **134 个角标（66 条 References）** / 15 张裁剪图。

## 2. 引用角标系统（前端）

- `SourceRefLink`：正文 `[n](#ref-n)` 渲染为上标数字角标
  - **hover**：浮框显示出处（章/节/页码/图表号）+ 原文摘录
  - **点击**：打开来源详情（`?modal=source&hl=<quote>`），顶部显示**琥珀色定位卡**：
    出处标签 + 原文中命中的上下文（命中段 mark 高亮，前后各 160 字），可一键清除
- `findQuoteContext` 容错定位：空白折叠 + **pdfplumber 重复字符折叠**
  （on-parsed full_text 存在 "AAnnnnuuaall" 式重复，实测 ar2026e）+ 忽略大小写；
  匹配失败降级为仅显示引用原文
- agent 侧 `submit_report` 继续清洗 URL 前导不可见空白

## 3. 保存到笔记（见解 → 笔记本）

- 见解卡片与见解对话框新增「保存到笔记」按钮
- 调 ON **原生端点** `POST /insights/{id}/save-as-note`（上游已有，零后端改动）
- 目标笔记本自动选择：来源所属第一个笔记本
- 端到端实测：note 创建成功（"核心观点 from source BIS Annual Economic Report..."）

## 涉及文件

- agent：`app/agents.py`（报告模板 F1-F7 硬性格式：总述/章节要点/Box 提炼/角标/References 原文摘录）
- 前端：`SourceRefLink.tsx`（新）、`lib/utils/source-refs.ts`（新，含 6 个单测）、
  `SourceInsightDialog.tsx`（角标渲染 + 保存按钮）、`SourceDetailContent.tsx`
  （hl 定位卡 + 卡片保存按钮 + Tabs 受控）、`lib/api/insights.ts`（saveAsNote）
- i18n：6 个新 key × 15 locales（含此前遗漏的 zh-TW）

## 验证

- agent pytest 39 passed；前端 tsc 0 错误、lint 0 错误（7 warning 均为既有）；
  vitest 组件 14 passed + 工具 6 passed（locale unused-key 扫描为既有环境性超时，非本次引入）
- 全链路：报告渲染 → 角标 hover → 点击跳转定位卡 → 保存到笔记
