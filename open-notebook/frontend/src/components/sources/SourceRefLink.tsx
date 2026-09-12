'use client'

// 引用角标组件：报告正文中 [1](#ref-1) 链接渲染为上标小角标。
// - hover：浮框展示出处（章/节/页码/图表）+ 原文摘录
// - 点击：打开所属来源详情并带 hl 参数定位高亮原文

import { useRouter, usePathname, useSearchParams } from 'next/navigation'
import { ExternalLink, Quote } from 'lucide-react'
import type { RefData } from '@/lib/utils/source-refs'
import { useTranslation } from '@/lib/hooks/use-translation'

interface SourceRefLinkProps {
  n: string
  data: RefData | undefined
  sourceId?: string
}

export function SourceRefLink({ n, data, sourceId }: SourceRefLinkProps) {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const { t } = useTranslation()

  const goSource = () => {
    if (!sourceId) return
    const params = new URLSearchParams(searchParams?.toString() || '')
    params.set('modal', 'source')
    params.set('id', sourceId)
    if (data?.quote) params.set('hl', data.quote)
    if (data) params.set('hl_label', `${data.source} ${data.page}`.trim())
    router.push(`${pathname}?${params.toString()}`, { scroll: false })
  }

  return (
    <span className="inline-block relative align-super mx-px group">
      <button
        type="button"
        onClick={goSource}
        data-ref={n}
        className="inline-flex items-center justify-center h-4 min-w-4 px-1 text-[10px] leading-none rounded-full bg-muted text-teal border border-teal/40 hover:bg-teal hover:text-white transition-colors cursor-pointer"
        aria-label={t('sources.refGoSource')}
      >
        {n}
      </button>
      {data && (
        <span
          className="hidden group-hover:block absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-80 max-w-[80vw]
                     rounded-lg border border-border bg-popover shadow-lg p-3 text-left normal-case"
          role="tooltip"
        >
          <span className="block text-xs font-medium text-muted-foreground mb-1">
            {data.source} {data.page}
            {data.graph && data.graph !== '—' ? ` · ${data.graph}` : ''}
          </span>
          <span className="flex items-start gap-1.5 text-xs leading-relaxed text-foreground">
            <Quote className="h-3 w-3 mt-0.5 shrink-0 text-teal" />
            <span className="break-words line-clamp-6">{data.quote}</span>
          </span>
          <span className="mt-2 flex items-center gap-1 text-[11px] text-teal">
            <ExternalLink className="h-3 w-3" />
            {t('sources.refGoSource')}
          </span>
        </span>
      )}
    </span>
  )
}
