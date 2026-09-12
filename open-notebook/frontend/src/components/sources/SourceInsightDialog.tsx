'use client'

import { useState, useEffect, useMemo } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { FileText, NotebookPen } from 'lucide-react'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { MarkdownRenderer } from '@/components/ui/markdown-renderer'
import { SourceRefLink } from './SourceRefLink'
import { parseReferences } from '@/lib/utils/source-refs'
import { useInsight } from '@/lib/hooks/use-insights'
import { useSource } from '@/lib/hooks/use-sources'
import { useModalManager } from '@/lib/hooks/use-modal-manager'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useNotebooks } from '@/lib/hooks/use-notebooks'
import { insightsApi } from '@/lib/api/insights'
import { toast } from 'sonner'
import { ContentUnavailable } from '@/components/common/ContentUnavailable'
import { isNotFoundError } from '@/lib/utils/error-handler'

interface SourceInsightDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  insight?: {
    id: string
    insight_type?: string
    content?: string
    created?: string | null
    source_id?: string
  }
  onDelete?: (insightId: string) => Promise<void>
}

export function SourceInsightDialog({ open, onOpenChange, insight, onDelete }: SourceInsightDialogProps) {
  const { t } = useTranslation()
  const { openModal } = useModalManager()
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [savingNote, setSavingNote] = useState(false)

  // Ensure insight ID has 'source_insight:' prefix for API calls
  const insightIdWithPrefix = insight?.id
    ? (insight.id.includes(':') ? insight.id : `source_insight:${insight.id}`)
    : ''

  const { data: fetchedInsight, isLoading, isError, error } = useInsight(insightIdWithPrefix, { enabled: open && !!insight?.id })

  // Use fetched data if available, otherwise fall back to passed-in insight.
  // On fetch error there is nothing trustworthy to show (the passed-in data
  // may reference a deleted item), so every derived field goes blank here.
  const displayInsight = isError ? undefined : (fetchedInsight ?? insight)

  // Get source_id from fetched data (preferred) or passed-in insight
  const sourceId = displayInsight?.source_id

  // v0.0.5 引用角标：解析文末 References 表格，正文 [n](#ref-n) 链接
  // 渲染为角标组件（hover 出处 / 点击跳原文）
  const refs = useMemo(
    () => parseReferences(displayInsight?.content ?? ''),
    [displayInsight?.content]
  )

  const handleViewSource = () => {
    if (sourceId) {
      openModal('source', sourceId)
    }
  }

  // 保存为笔记：优先来源所属笔记本（source.notebooks[0]），否则第一个笔记本
  const { data: notebooks } = useNotebooks()
  const { data: sourceOfInsight } = useSource(sourceId ?? '')
  const handleSaveAsNote = async () => {
    const targetNotebook =
      sourceOfInsight?.notebooks?.[0] ?? notebooks?.[0]?.id
    if (!insightIdWithPrefix || !targetNotebook) {
      toast.error(t('sources.notebookRequired'))
      return
    }
    setSavingNote(true)
    try {
      await insightsApi.saveAsNote(insightIdWithPrefix, targetNotebook)
      toast.success(t('sources.savedAsNote'))
    } catch (err) {
      console.error('Failed to save insight as note:', err)
      toast.error(t('sources.saveAsNoteFailed'))
    } finally {
      setSavingNote(false)
    }
  }

  const handleDelete = async () => {
    if (!insight?.id || !onDelete) return
    setIsDeleting(true)
    try {
      await onDelete(insight.id)
      onOpenChange(false)
    } finally {
      setIsDeleting(false)
      setShowDeleteConfirm(false)
    }
  }

  // Reset delete confirmation when dialog closes
  useEffect(() => {
    if (!open) {
      setShowDeleteConfirm(false)
    }
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl max-h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center justify-between gap-2 pr-8">
            <span>{t('sources.sourceInsight')}</span>
            <div className="flex items-center gap-2">
              {displayInsight?.insight_type && (
                <Badge variant="outline" className="gap-1.5 text-xs uppercase">
                  <span className="h-1.5 w-1.5 rounded-full bg-teal" aria-hidden="true" />
                  {displayInsight.insight_type}
                </Badge>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={handleSaveAsNote}
                disabled={savingNote}
                className="gap-1"
                title={t('sources.saveAsNote')}
              >
                {savingNote ? (
                  <LoadingSpinner className="h-3 w-3" />
                ) : (
                  <NotebookPen className="h-3 w-3" />
                )}
                {t('sources.saveAsNote')}
              </Button>
              {sourceId && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleViewSource}
                  className="gap-1"
                >
                  <FileText className="h-3 w-3" />
                  {t('sources.viewSource')}
                </Button>
              )}
            </div>
          </DialogTitle>
        </DialogHeader>

        {showDeleteConfirm ? (
          <div className="flex flex-col items-center justify-center py-8 gap-4">
            <p className="text-center text-muted-foreground">
              {t('sources.deleteInsightConfirm').split(/[?？]/)[0]}?<br />
              <span className="text-sm">{t('sources.deleteInsightConfirm').split(/[?？]/)[1]?.trim() || t('common.deleteForever')}</span>
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => setShowDeleteConfirm(false)}
                disabled={isDeleting}
              >
                {t('common.cancel')}
              </Button>
              <Button
                variant="destructive"
                onClick={handleDelete}
                disabled={isDeleting}
              >
                {isDeleting ? t('common.deleting') : t('common.delete')}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto min-h-0">
            {isLoading ? (
              <div className="flex items-center justify-center py-10">
                <span className="text-sm text-muted-foreground">{t('common.loading')}</span>
              </div>
            ) : isError ? (
              <ContentUnavailable
                variant={isNotFoundError(error) ? 'not-found' : 'error'}
                onClose={() => onOpenChange(false)}
              />
            ) : displayInsight ? (
              <MarkdownRenderer
                components={{
                  a: ({ href, children }) => {
                    const m = typeof href === 'string' ? /#ref-(\d+)/.exec(href) : null
                    if (m) {
                      return (
                        <SourceRefLink
                          n={m[1]}
                          data={refs.get(m[1])}
                          sourceId={sourceId}
                        />
                      )
                    }
                    return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
                  },
                }}
              >
                {displayInsight.content}
              </MarkdownRenderer>
            ) : (
              <p className="text-sm text-muted-foreground">{t('sources.noInsightSelected')}</p>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
