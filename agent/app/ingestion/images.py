"""图表检测与精准裁剪。

BIS 图表是矢量图（不是嵌入位图），pypdf 提嵌入图拿到的是空手/碎片。
两步走：

1. **判页**：矢量路径密度（实测纯文本页 ~12 个 path 对象，图表页 145+），
   宁可多判（后续裁剪/VLM 能甄别），不可漏。
2. **裁剪**：不再截整页——收集页面上 path/image 对象的 bbox，按空间
   邻近聚簇，每簇外接矩形即一个图表区域（BIS 三联图 Graph N.A/B/C 紧排
   会自然聚为一簇），在 200DPI 整页渲染上按比例裁出单图 PNG。
   图注（"Graph N.X" 文本行，位于图下方）通过把簇底部向下扩展数个
   文本行高度一并纳入。

坐标：pdfium 对象 bbox 是 PDF 坐标（原点左下、y 向上）；渲染图像原点
在左上。裁剪前做 y 翻转换算。
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

# ---- 裁剪聚类参数（pt，A4 宽 595pt）----
# 两对象 bbox 间距小于此值视为同一图表簇（图表内元素紧密排布）
CLUSTER_GAP = 22
# 簇最小面积（pt²）：过滤孤线等噪声（单个子图约 100x70pt = 7000）
MIN_REGION_AREA = 4000
# 簇 bbox 向外扩展：左右含轴标签，底部多扩以纳入图注（约 3 行小字）
PAD_X, PAD_TOP, PAD_BOTTOM = 10, 8, 30
# 页眉/页脚带（pt）：带内矢量对象（BIS 页眉装饰线每页出现在 y≈713-779）
# 不是图表，作聚类种子前剔除——否则每页多出一张 482x66 的页眉截图
HEADER_BAND, FOOTER_BAND = 80, 55


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


def _merge_boxes(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _boxes_close(a: tuple, b: tuple, gap: float = CLUSTER_GAP) -> bool:
    """两 bbox 空间邻近（间隙均小于 gap，含相交）。"""
    return (
        b[0] - a[2] <= gap
        and a[0] - b[2] <= gap
        and b[1] - a[3] <= gap
        and a[1] - b[3] <= gap
    )


def extract_chart_regions(page: pdfium.PdfPage) -> list[tuple]:
    """页面 → 图表区域 bbox 列表（PDF 坐标，已含边距扩展）。

    只用 path/image 对象做聚类种子（图表是矢量密集区；正文文本不参与，
    避免把段落聚进来）。小面积簇过滤噪声。
    """
    seeds: list[tuple] = []
    page_w, page_h = page.get_size()
    try:
        for obj in page.get_objects():
            if obj.type not in (pdfium.raw.FPDF_PAGEOBJ_PATH, pdfium.raw.FPDF_PAGEOBJ_IMAGE):
                continue
            try:
                pos = obj.get_bounds()  # (left, bottom, right, top)
            except Exception:  # 个别对象无位置信息
                logger.debug("obj bounds failed", exc_info=True)
                continue
            if pos[2] - pos[0] < 1 and pos[3] - pos[1] < 1:
                continue  # 零尺寸对象
            # 页眉/页脚带的装饰元素不作种子（见 HEADER_BAND 说明）
            if pos[1] > page_h - HEADER_BAND or pos[3] < FOOTER_BAND:
                continue
            seeds.append(tuple(pos))
    except Exception:  # 对象枚举失败退回整页
        logger.debug("object enum failed", exc_info=True)
        return []

    # 贪心聚类：反复合并邻近簇直到稳定（对象数百级，两轮即收敛）
    clusters = list(seeds)
    merged = True
    while merged:
        merged = False
        out: list[tuple] = []
        for box in clusters:
            hit = next((c for c in out if _boxes_close(c, box)), None)
            if hit is not None:
                out[out.index(hit)] = _merge_boxes(hit, box)
                merged = True
            else:
                out.append(box)
        clusters = out

    page_w, page_h = page.get_size()
    regions = []
    for c in clusters:
        area = (c[2] - c[0]) * (c[3] - c[1])
        if area < MIN_REGION_AREA:
            continue
        # 边距扩展（底部多扩容纳图注），并裁到页面范围内
        x0 = max(c[0] - PAD_X, 0)
        y0 = max(c[1] - PAD_BOTTOM, FOOTER_BAND)
        x1 = min(c[2] + PAD_X, page_w)
        y1 = min(c[3] + PAD_TOP, page_h - HEADER_BAND)
        regions.append((x0, y0, x1, y1))
    return regions


def render_page_regions(
    pdf_doc: pdfium.PdfDocument,
    page_no: int,
    out_dir: Path,
) -> list[str]:
    """图表页 → 该页各图表区域的裁剪 PNG 路径列表。

    区域为空时退回整页截图（保底不丢图）。
    """
    page = pdf_doc[page_no]
    regions = extract_chart_regions(page)
    bitmap = page.render(scale=RENDER_SCALE)
    image = bitmap.to_pil()
    page_w, page_h = page.get_size()
    sx, sy = image.width / page_w, image.height / page_h

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    if not regions:
        p = out_dir / f"page_{page_no + 1:03d}_full.png"
        image.save(p, format="PNG")
        return [str(p)]
    for i, (x0, y0, x1, y1) in enumerate(regions, start=1):
        # PDF 坐标（左下原点）→ 图像像素（左上原点）：y 翻转
        crop = (
            int(x0 * sx),
            int((page_h - y1) * sy),
            int(x1 * sx),
            int((page_h - y0) * sy),
        )
        clip = image.crop(crop)
        if clip.width < 50 or clip.height < 50:  # 裁剪异常保底跳过
            continue
        p = out_dir / f"page_{page_no + 1:03d}_{i}.png"
        clip.save(p, format="PNG")
        paths.append(str(p))
    return paths


def attach_images(
    sections: list[dict],
    pdf_doc: pdfium.PdfDocument,
    source_id: str,
    out_root: Path,
) -> int:
    """检测图表页 → 裁剪图表区域 PNG → 按页码归属挂到对应 section。

    归属规则：section 的 [page_start, page_end] 与图表页相交即挂载。
    跨节的图表页会挂给多个 section——worker 各自看到相关图表，
    终审去重，覆盖度优先。

    Returns:
        渲染的 PNG 总数（同一页可产出多张裁剪图）。
    """
    chart_pages = detect_chart_pages(pdf_doc)
    if not chart_pages:
        return 0

    out_dir = Path(out_root) / source_id
    rendered: dict[int, list[str]] = {}
    for pno in sorted(chart_pages):
        try:
            rendered[pno] = render_page_regions(pdf_doc, pno, out_dir)
        except Exception:
            logger.warning("chart page %s render failed", pno, exc_info=True)

    for s in sections:
        start = s.get("page_start")
        end = s.get("page_end")
        if start is None or end is None:
            continue
        for pno, img_paths in rendered.items():
            if start <= pno <= end:
                s.setdefault("images", []).extend(img_paths)
    return sum(len(v) for v in rendered.values())
