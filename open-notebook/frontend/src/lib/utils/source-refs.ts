// 报告 References 表格解析与原文定位（v0.0.5 引用角标系统）。
//
// agent 生成的结构化报告以固定格式表格收尾：
//   | 编号 | 出处（章 / 节） | 页码 | 涉及图表 | 原文摘录 |
//   | [1](#ref-1) | I. ... / A resilient start | p.17 | Graph 2.A | the effective ... |
// 正文观点尾部带 [1](#ref-1) 角标链接。本模块解析表格供角标组件
// hover 展示出处、点击跳转原文（hl 参数 → source 详情定位高亮）。

export interface RefData {
  n: string
  source: string
  page: string
  graph: string
  quote: string
}

export interface QuoteContext {
  before: string
  matched: string
  after: string
}

const REF_ROW = /\|\s*\[(\d+)\]\(#ref-\d+\)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|/
const PAD = 160

/** 解析报告文末 References 表格 → { 编号 → 引用数据 }。 */
export function parseReferences(content: string): Map<string, RefData> {
  const refs = new Map<string, RefData>()
  if (!content) return refs
  for (const line of content.split('\n')) {
    const m = REF_ROW.exec(line.trim())
    if (!m) continue
    refs.set(m[1], {
      n: m[1],
      source: m[2].trim(),
      page: m[3].trim(),
      graph: m[4].trim(),
      quote: m[5].trim(),
    })
  }
  return refs
}

/**
 * 单遍规范化扫描：空白折叠为单空格、连续重复字符折叠、小写化。
 * 返回规范化串与「规范化字符 → 原文下标」映射（折叠只删不改序）。
 */
function canonize(s: string): { text: string; idx: number[] } {
  const chars: string[] = []
  const idx: number[] = []
  let prev = ''
  for (let i = 0; i < s.length; i++) {
    const raw = s[i]
    if (/\s/.test(raw)) {
      if (prev === ' ') continue // 空白 run 折叠
      chars.push(' ')
      idx.push(i)
      prev = ' '
      continue
    }
    const c = raw.toLowerCase()
    if (c === prev) continue // 连续重复折叠（含 "AA"→"a"）
    chars.push(c)
    idx.push(i)
    prev = c
  }
  return { text: chars.join(''), idx }
}

/**
 * 在全文中定位引用原文，返回高亮上下文（前文/命中段/后文）。
 *
 * 规范化匹配（空白折叠 + 重复字符折叠 + 忽略大小写）一次完成——
 * pdfplumber 提取的 full_text 存在 "AAnnnnuuaall" 式重复（实测
 * ar2026e），精确匹配注定失败，重复折叠是必需而非可选。
 * 失败返回 null（调用方降级为只显示引用卡，不定位）。
 */
export function findQuoteContext(
  fullText: string,
  quote: string
): QuoteContext | null {
  if (!fullText || !quote) return null
  const f = canonize(fullText)
  const q = canonize(quote).text
  const at = f.text.indexOf(q)
  if (at < 0 || q.length === 0) return null
  const start = f.idx[at]
  const end = f.idx[at + q.length - 1] + 1
  return {
    before: fullText.slice(Math.max(0, start - PAD), start),
    matched: fullText.slice(start, end),
    after: fullText.slice(end, end + PAD),
  }
}
