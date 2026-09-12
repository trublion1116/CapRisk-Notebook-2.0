"""图表页检测与渲染。

BIS 图表是矢量图（不是嵌入位图），pypdf 提嵌入图拿到的是空手/碎片，
因此用 pypdfium2 把**图表所在整页**渲染成 PNG，交给视觉模型解读。

图表页判定以**矢量路径密度为主信号**（实测：纯文本页 ~12 个 path
对象，图表页 145-231 个），图注文本（"Graph N"）只作辅助——正文
引用与图注同形（"Graph 1.A)." 在正文句子里也出现），单独用文本判
会误报。宁可多渲染（视觉模型能甄别非图表），不可漏。
"""

import logging
from pathlib import Path

import pypdfium2 as pdfium

logger = logging.getLogger(__name__)

# 实测纯文本页 ~12 paths、图表页 145+，取 60 留足余量
CHART_PATH_THRESHOLD = 60
# 渲染精度：200 DPI 实测（2026-09-11, ar2026e p18）VLM 描述含全部关键要素
# （图号/标题/坐标/图例/数值）且 ~20s/张；300 DPI 描述质量相同但慢一倍
# （34s，708KB）；150 DPI 有丢小字注释风险。
RENDER_SCALE = 200 / 72


def detect_chart_pages(pdf_doc: pdfium.PdfDocument) -> dict[int, int]:
    """返回 {页码: 矢量路径数}，只含超过阈值的页。"""
    chart_pages: dict[int, int] = {}
    for pno, page in enumerate(pdf_doc):
        try:
            n_paths = sum(
                1 for obj in page.get_objects() if obj.type == pdfium.raw.FPDF_PAGEOBJ_PATH
            )
        except Exception:  # 单页对象统计失败跳过
            logger.debug("page %s object stats failed", pno, exc_info=True)
            continue
        if n_paths >= CHART_PATH_THRESHOLD:
            chart_pages[pno] = n_paths
    return chart_pages


def render_page(pdf_doc: pdfium.PdfDocument, page_no: int, out_path: Path) -> Path:
    """渲染单页为 PNG（300 DPI）。"""
    page = pdf_doc[page_no]
    bitmap = page.render(scale=RENDER_SCALE)
    image = bitmap.to_pil()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, format="PNG")
    return out_path


def attach_images(
    sections: list[dict],
    pdf_doc: pdfium.PdfDocument,
    source_id: str,
    out_root: Path,
) -> int:
    """检测图表页 → 渲染 PNG → 按页码归属挂到对应 section。

    归属规则：section 的 [page_start, page_end] 与图表页相交即挂载。
    跨节的图表页会挂给多个 section——worker 各自看到相关图表，
    终审去重，覆盖度优先。

    Returns:
        渲染的 PNG 总数（去重后，同一页只渲染一次）。
    """
    chart_pages = detect_chart_pages(pdf_doc)
    if not chart_pages:
        return 0

    out_dir = Path(out_root) / source_id
    rendered: dict[int, str] = {}
    for pno in sorted(chart_pages):
        try:
            path = render_page(pdf_doc, pno, out_dir / f"page_{pno + 1:03d}.png")
            rendered[pno] = str(path)
        except Exception:
            logger.warning("chart page %s render failed", pno, exc_info=True)

    for s in sections:
        start = s.get("page_start")
        end = s.get("page_end")
        if start is None or end is None:
            continue
        for pno, img_path in rendered.items():
            if start <= pno <= end:
                s.setdefault("images", []).append(img_path)
    return len(rendered)
