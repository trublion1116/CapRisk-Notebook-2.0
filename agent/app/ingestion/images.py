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
import re
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
# 簇 bbox 向外扩展：左右含轴标签；上下方向小 padding——图注（"Graph
# N.X" 文本行）由 _merge_captions 按文本层坐标精准并入，不靠盲扩
PAD_X, PAD_TOP, PAD_BOTTOM = 10, 8, 8
# 图注并入搜索半径（pt）：图注行中心距簇边界的最大距离（图注可在图
# 上方或下方——实测 E1 图注在图形上方，B1 在下方，方向不固定）
CAPTION_MERGE_RADIUS = 50
# 页眉/页脚带（pt）：带内矢量对象（BIS 页眉装饰线每页出现在 y≈713-779）
# 不是图表，作聚类种子前剔除——否则每页多出一张 482x66 的页眉截图
HEADER_BAND, FOOTER_BAND = 80, 55
# 簇占页面面积超过此比例视为聚合过度（Box 专题页装饰元素会链式连接
# 整页内容，实测 Box B1/E1 页聚到 57-69%）——用更小 gap 递归细分
MAX_REGION_RATIO = 0.45
# 细分的 gap 序列（逐级减半）：图表内部元素间距远小于图表间间距
REFINE_GAPS = (12, 7, 4)


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


def _cluster_boxes(
    boxes: list[tuple], gap: float
) -> list[tuple[tuple, list[tuple]]]:
    """贪心聚类（链式传播）。返回 [(簇 bbox, 成员 bbox 列表), ...]。

    成员保留在簇上：超限簇细分时需要知道簇内原始对象。
    """
    clusters: list[tuple[tuple, list[tuple]]] = [(b, [b]) for b in boxes]
    merged = True
    while merged:
        merged = False
        out: list[tuple[tuple, list[tuple]]] = []
        for box, members in clusters:
            idx = next(
                (i for i, (c, _) in enumerate(out) if _boxes_close(c, box, gap)),
                None,
            )
            if idx is not None:
                c, m = out[idx]
                out[idx] = (_merge_boxes(c, box), m + members)
                merged = True
            else:
                out.append((box, members))
        clusters = out
    return clusters


# 细长贯穿线过滤：宽超过页面一半且厚度 <3pt 的对象（box 分隔线/表格
# 线）bbox 会与横向所有对象"相交"，一条线就能把整页链式连通——实测
# p24 即使 gap=0 也聚成 64% 单簇的根因。图表轴线因宽度 < 半页不受影响。
SPAN_LINE_MAX_THICK = 3.0
SPAN_LINE_MIN_RATIO = 0.5


def _is_span_line(box: tuple, page_w: float, page_h: float) -> bool:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    return (
        w > SPAN_LINE_MIN_RATIO * page_w and h < SPAN_LINE_MAX_THICK
    ) or (h > SPAN_LINE_MIN_RATIO * page_h and w < SPAN_LINE_MAX_THICK)


def _caption_anchors(page: pdfium.PdfPage) -> list[tuple]:
    """文本层图注行锚点：『Graph N』/『Graph X1』等编号首字符的 bbox。

    只取编号首字符（一行图注的 y 即行 y，x 用于水平重叠判断）。
    """
    tp = page.get_textpage()
    try:
        text = tp.get_text_bounded()
        anchors = []
        for m in re.finditer(r"Graph [A-Z]?\d", text):
            try:
                b = tp.get_charbox(m.start())  # (l, b, r, t)
            except Exception:  # 单字符取坐标失败跳过
                logger.debug("charbox failed at %d", m.start(), exc_info=True)
                continue
            anchors.append((b[0], b[1], b[2], b[3]))
        return anchors
    except Exception:  # 文本层不可用当无图注
        logger.debug("text layer unavailable", exc_info=True)
        return []


def _merge_captions(
    regions: list[tuple], captions: list[tuple]
) -> list[tuple]:
    """把图注行锚点并入水平重叠、垂直邻近的区域（扩展 y 边界）。

    一条图注只并入最近的一个区域；无匹配区域的孤立图注忽略
    （可能是正文引用，正文里 "Graph N" 也出现——但正文引用与任何
    矢量簇都不邻近，天然被 CAPTION_MERGE_RADIUS 过滤）。
    """
    if not captions or not regions:
        return regions
    out = []
    used = [False] * len(captions)
    for r in regions:
        x0, y0, x1, y1 = r
        for i, c in enumerate(captions):
            if used[i]:
                continue
            # 水平区间需有重叠（图注行与图形区同列）
            if c[0] > x1 or c[2] < x0:
                continue
            # 垂直邻近：图注在区域上方或下方 radius 内（含已在区域内）
            if (y0 - CAPTION_MERGE_RADIUS) <= c[1] <= (y1 + CAPTION_MERGE_RADIUS):
                x0, y0 = min(x0, c[0]), min(y0, c[1])
                x1, y1 = max(x1, c[2]), max(y1, c[3])
                used[i] = True
        out.append((x0, y0, x1, y1))
    return out


def extract_chart_regions(page: pdfium.PdfPage) -> list[tuple]:
    """页面 → 图表区域 bbox 列表（PDF 坐标，已含边距扩展）。

    只用 path/image 对象做聚类种子（图表是矢量密集区；正文文本不参与，
    避免把段落聚进来）。小面积簇过滤噪声。占页面超过 MAX_REGION_RATIO
    的簇（Box 专题页装饰元素链式连接整页的过度聚合）用逐级减半的 gap
    递归细分——图表内部元素间距（<4pt）远小于图表/装饰元素之间，细分
    后各子图/图组自然分离。
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
            # 贯穿性细线不作种子（见 _is_span_line 说明）
            if _is_span_line(pos, page_w, page_h):
                continue
            seeds.append(tuple(pos))
    except Exception:  # 对象枚举失败退回整页
        logger.debug("object enum failed", exc_info=True)
        return []

    if not seeds:
        return []

    page_area = page_w * page_h
    final: list[tuple] = []

    def _area(c: tuple) -> float:
        return (c[2] - c[0]) * (c[3] - c[1])

    def _refine(cbox: tuple, members: list[tuple], gap_idx: int) -> None:
        """超限簇细分：小 gap 只会分得更细；分不开说明是紧密单体，保留。"""
        if _area(cbox) <= MAX_REGION_RATIO * page_area:
            final.append(cbox)
            return
        if gap_idx >= len(REFINE_GAPS):
            # 细分到头仍超限：整版图组，保留兜底（好过丢图）
            final.append(cbox)
            return
        subs = _cluster_boxes(members, REFINE_GAPS[gap_idx])
        if len(subs) <= 1:
            # 元素间距 ≤ 当前 gap：真正的整版大图，保留
            final.append(cbox)
            return
        for sub_box, sub_members in subs:
            _refine(sub_box, sub_members, gap_idx + 1)

    for cbox, members in _cluster_boxes(seeds, CLUSTER_GAP):
        _refine(cbox, members, 0)

    # 图注文本行并入（先于 PAD 扩展：用簇原始边界判断邻近更准）
    final = [c for c in final if _area(c) >= MIN_REGION_AREA]
    final = _merge_captions(final, _caption_anchors(page))

    regions = []
    for c in final:
        # 边距扩展（图注已精准并入，只剩小 padding），裁到页面范围内
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
