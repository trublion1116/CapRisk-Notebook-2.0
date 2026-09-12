# 效果展示（Showcase）

每个迭代版本一个子目录（`vX.Y.Z`），存放该版本跑出的实际效果：

- `insights.md`：该版本从报告中提取的核心观点（由 `POST /extract` 生成，直接导出自 OpenNotebook）
- `images/`：报告内嵌的图表截图（与 insights.md 的相对路径 `images/...` 自包含，GitHub 线上可直接渲染）
- 其他展示材料（如与来源文档的对话分析、Langfuse trace 链接等）

版本与 git tag 一一对应：`git tag v0.0.1` 关联 `showcase/v0.0.1/`。
