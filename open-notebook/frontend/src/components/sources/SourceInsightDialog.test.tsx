import { render, screen, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SourceInsightDialog } from './SourceInsightDialog'
import { useInsight } from '@/lib/hooks/use-insights'

// useTranslation is mocked globally in setup.ts (t returns the key string)

vi.mock('@/lib/hooks/use-insights', () => ({
  useInsight: vi.fn(),
}))

vi.mock('@/lib/hooks/use-modal-manager', () => ({
  useModalManager: () => ({ openModal: vi.fn() }),
}))

// v0.0.5: dialog now reads source/notebooks (save-as-note) — stub the hooks
// so no QueryClientProvider network layer is exercised in these tests.
vi.mock('@/lib/hooks/use-notebooks', () => ({
  useNotebooks: vi.fn().mockReturnValue({ data: [{ id: 'notebook:1' }] }),
}))

vi.mock('@/lib/hooks/use-sources', () => ({
  useSource: vi.fn().mockReturnValue({
    data: { id: 'source:1', notebooks: ['notebook:1'] },
  }),
}))

const mockUseInsight = vi.mocked(useInsight)

const notFoundError = Object.assign(new Error('Request failed with status code 404'), {
  isAxiosError: true,
  response: { status: 404 },
})

const networkError = Object.assign(new Error('Network Error'), {
  isAxiosError: true,
  response: undefined,
})

type UseInsightResult = ReturnType<typeof useInsight>

const asResult = (value: Partial<UseInsightResult>) => value as UseInsightResult

const renderDialog = (ui: React.ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
  )

describe('SourceInsightDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the shared not-found state when the insight returns 404', () => {
    mockUseInsight.mockReturnValue(
      asResult({ data: undefined, isLoading: false, isError: true, error: notFoundError })
    )

    renderDialog(
      <SourceInsightDialog
        open={true}
        onOpenChange={vi.fn()}
        insight={{ id: 'insight-1', insight_type: '', content: '' }}
      />
    )

    expect(screen.getByTestId('content-unavailable')).toBeInTheDocument()
    expect(screen.getByText('common.contentUnavailable.notFoundTitle')).toBeInTheDocument()
    expect(screen.getByText('common.contentUnavailable.notFoundDescription')).toBeInTheDocument()
    // No ghost fallback content and no "view source" affordance
    expect(screen.queryByText('sources.viewSource')).not.toBeInTheDocument()
  })

  it('shows the shared load-error state for non-404 failures', () => {
    mockUseInsight.mockReturnValue(
      asResult({ data: undefined, isLoading: false, isError: true, error: networkError })
    )

    renderDialog(
      <SourceInsightDialog
        open={true}
        onOpenChange={vi.fn()}
        insight={{ id: 'insight-1', insight_type: '', content: '' }}
      />
    )

    expect(screen.getByText('common.contentUnavailable.errorTitle')).toBeInTheDocument()
    expect(
      screen.queryByText('common.contentUnavailable.notFoundTitle')
    ).not.toBeInTheDocument()
  })

  it('closes the dialog from the not-found state close button', () => {
    mockUseInsight.mockReturnValue(
      asResult({ data: undefined, isLoading: false, isError: true, error: notFoundError })
    )
    const onOpenChange = vi.fn()

    renderDialog(
      <SourceInsightDialog
        open={true}
        onOpenChange={onOpenChange}
        insight={{ id: 'insight-1', insight_type: '', content: '' }}
      />
    )

    within(screen.getByTestId('content-unavailable')).getByText('common.close').click()
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('renders the insight content when the fetch succeeds', () => {
    mockUseInsight.mockReturnValue(
      asResult({
        data: {
          id: 'insight-1',
          source_id: 'source:1',
          insight_type: 'summary',
          content: 'Fetched insight content',
          created: null,
          updated: null,
        },
        isLoading: false,
        isError: false,
        error: null,
      })
    )

    renderDialog(
      <SourceInsightDialog
        open={true}
        onOpenChange={vi.fn()}
        insight={{ id: 'insight-1', insight_type: '', content: '' }}
      />
    )

    expect(screen.getByText('Fetched insight content')).toBeInTheDocument()
    expect(screen.queryByTestId('content-unavailable')).not.toBeInTheDocument()
  })
})
