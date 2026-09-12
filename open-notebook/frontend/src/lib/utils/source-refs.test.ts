import { describe, it, expect } from 'vitest'
import { parseReferences, findQuoteContext } from './source-refs'

const REPORT = `# 报告

* **要点**：说明 [1](#ref-1)。

## References（观点出处索引）

| 编号 | 出处（章 / 节） | 页码 | 涉及图表 | 原文摘录 |
|---|---|---|---|---|
| [1](#ref-1) | I. Progress / A resilient start | p.17 | Graph 2.A | the effective average US tariff rate stabilised at 10% |
| [2](#ref-2) | III. ... / Box E | p.124 | — | Such measures are, however, likely to be imperfect |
`

describe('parseReferences', () => {
  it('parses reference table rows into a map keyed by number', () => {
    const refs = parseReferences(REPORT)
    expect(refs.size).toBe(2)
    expect(refs.get('1')?.source).toBe('I. Progress / A resilient start')
    expect(refs.get('1')?.page).toBe('p.17')
    expect(refs.get('1')?.quote).toContain('tariff rate stabilised')
    expect(refs.get('2')?.graph).toBe('—')
  })

  it('returns empty map for content without a table', () => {
    expect(parseReferences('no table here').size).toBe(0)
    expect(parseReferences('').size).toBe(0)
  })
})

describe('findQuoteContext', () => {
  const FULL = 'Annual Economic Report of the BIS says growth was resilient in 2025.'

  it('locates an exact substring with highlight context', () => {
    const ctx = findQuoteContext(FULL, 'growth was resilient')
    expect(ctx).not.toBeNull()
    expect(ctx!.matched.toLowerCase()).toBe('growth was resilient')
    expect(ctx!.before).toContain('BIS says')
    expect(ctx!.after).toContain('2025')
  })

  it('matches across whitespace and case differences', () => {
    const ctx = findQuoteContext(FULL, '  GROWTH   WAS resilient ')
    expect(ctx).not.toBeNull()
  })

  it('matches pdfplumber-style doubled characters (AAnnnnuuaall)', () => {
    // on-parsed full_text 实测存在字符重复（ar2026e），折叠重复后应命中
    const doubled = 'The AAnnnnuuaall EEccoonnoommiicc Report covers 2026.'
    const ctx = findQuoteContext(doubled, 'Annual Economic Report')
    expect(ctx).not.toBeNull()
    expect(ctx!.matched).toBe('AAnnnnuuaall EEccoonnoommiicc Report')
  })

  it('returns null when quote is absent', () => {
    expect(findQuoteContext(FULL, 'nothing matches this')).toBeNull()
    expect(findQuoteContext('', 'x')).toBeNull()
    expect(findQuoteContext(FULL, '')).toBeNull()
  })
})
