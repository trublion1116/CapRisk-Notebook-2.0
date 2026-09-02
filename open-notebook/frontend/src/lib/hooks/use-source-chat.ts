'use client'

import { useState, useCallback, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getApiErrorMessage } from '@/lib/utils/error-handler'
import { useTranslation } from '@/lib/hooks/use-translation'
import { sourceChatApi } from '@/lib/api/source-chat'
import {
  SourceChatSession,
  SourceChatMessage,
  SourceChatContextIndicator,
  CreateSourceChatSessionRequest,
  UpdateSourceChatSessionRequest
} from '@/lib/types/api'

export function useSourceChat(sourceId: string) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<SourceChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [contextIndicators, setContextIndicators] = useState<SourceChatContextIndicator | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)

  // Fetch sessions
  const { data: sessions = [], isLoading: loadingSessions, refetch: refetchSessions } = useQuery<SourceChatSession[]>({
    queryKey: ['sourceChatSessions', sourceId],
    queryFn: () => sourceChatApi.listSessions(sourceId),
    enabled: !!sourceId
  })

  // Fetch current session with messages
  const { data: currentSession, refetch: refetchCurrentSession } = useQuery({
    queryKey: ['sourceChatSession', sourceId, currentSessionId],
    queryFn: () => sourceChatApi.getSession(sourceId, currentSessionId!),
    enabled: !!sourceId && !!currentSessionId
  })

  // Update messages when session changes.
  // Guarded while streaming: mid-stream refetches (e.g. window-focus) would
  // replace the live-streaming bubble with stale checkpoint history, losing
  // the in-flight answer. The post-stream refetch applies after
  // setIsStreaming(false).
  useEffect(() => {
    if (currentSession?.messages && !isStreaming) {
      setMessages(currentSession.messages)
    }
  }, [currentSession, isStreaming])

  // Auto-select most recent session when sessions are loaded
  useEffect(() => {
    if (sessions.length > 0 && !currentSessionId) {
      // Find most recent session (sessions are sorted by created date desc from API)
      const mostRecentSession = sessions[0]
      setCurrentSessionId(mostRecentSession.id)
    }
  }, [sessions, currentSessionId])

  // Create session mutation
  const createSessionMutation = useMutation({
    mutationFn: (data: Omit<CreateSourceChatSessionRequest, 'source_id'>) => 
      sourceChatApi.createSession(sourceId, data),
    onSuccess: (newSession) => {
      queryClient.invalidateQueries({ queryKey: ['sourceChatSessions', sourceId] })
      setCurrentSessionId(newSession.id)
      toast.success(t('chat.sessionCreated'))
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToCreateSession'))
    }
  })

  // Update session mutation
  const updateSessionMutation = useMutation({
    mutationFn: ({ sessionId, data }: { sessionId: string, data: UpdateSourceChatSessionRequest }) =>
      sourceChatApi.updateSession(sourceId, sessionId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sourceChatSessions', sourceId] })
      queryClient.invalidateQueries({ queryKey: ['sourceChatSession', sourceId, currentSessionId] })
      toast.success(t('chat.sessionUpdated'))
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToUpdateSession'))
    }
  })

  // Delete session mutation
  const deleteSessionMutation = useMutation({
    mutationFn: (sessionId: string) => 
      sourceChatApi.deleteSession(sourceId, sessionId),
    onSuccess: (_, deletedId) => {
      queryClient.invalidateQueries({ queryKey: ['sourceChatSessions', sourceId] })
      if (currentSessionId === deletedId) {
        setCurrentSessionId(null)
        setMessages([])
      }
      toast.success(t('chat.sessionDeleted'))
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToDeleteSession'))
    }
  })

  // Send message with streaming
  const sendMessage = useCallback(async (message: string, modelOverride?: string) => {
    let sessionId = currentSessionId

    // Auto-create session if none exists
    if (!sessionId) {
      try {
        const defaultTitle = message.length > 30 ? `${message.substring(0, 30)}...` : message
        const newSession = await sourceChatApi.createSession(sourceId, { title: defaultTitle })
        sessionId = newSession.id
        setCurrentSessionId(sessionId)
        queryClient.invalidateQueries({ queryKey: ['sourceChatSessions', sourceId] })
      } catch (err: unknown) {
        const error = err as { response?: { data?: { detail?: string } }, message?: string };
        console.error('Failed to create chat session:', error)
        toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToCreateSession'))
        return
      }
    }

    // Add user message optimistically
    const userMessage: SourceChatMessage = {
      id: `temp-${Date.now()}`,
      type: 'human',
      content: message,
      timestamp: new Date().toISOString()
    }
    setMessages(prev => [...prev, userMessage])
    setIsStreaming(true)

    // Streaming AI message (created on first event, then updated in place)
    const aiMessageId = `ai-${Date.now()}`

    const updateAiMessage = (apply: (msg: SourceChatMessage) => SourceChatMessage) => {
      setMessages(prev => {
        if (!prev.some(msg => msg.id === aiMessageId)) {
          return [...prev, apply({
            id: aiMessageId,
            type: 'ai',
            content: '',
            timestamp: new Date().toISOString()
          })]
        }
        return prev.map(msg => msg.id === aiMessageId ? apply(msg) : msg)
      })
    }

    try {
      const response = await sourceChatApi.sendMessage(sourceId, sessionId, {
        message,
        model_override: modelOverride
      })

      if (!response) {
        throw new Error('No response body')
      }

      const reader = response.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let streamError: string | null = null

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        // Keep the last incomplete line in the buffer
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const jsonStr = line.slice(6).trim()
          if (!jsonStr) continue

          let data: {
            type: string
            content?: string
            id?: string
            name?: string
            args?: Record<string, unknown>
            status?: 'running' | 'success' | 'error'
            data?: unknown
            message?: string
          }
          try {
            data = JSON.parse(jsonStr)
          } catch (e) {
            if (e instanceof SyntaxError) {
              console.error('Error parsing SSE data:', e, 'Line:', line)
              continue
            }
            throw e
          }

          if (data.type === 'token') {
            // Fork: streamed answer chunk from the agent service
            updateAiMessage(msg => ({ ...msg, content: msg.content + (data.content || '') }))
          } else if (data.type === 'tool_call') {
            updateAiMessage(msg => ({
              ...msg,
              toolCalls: [
                ...(msg.toolCalls || []),
                { id: data.id, name: data.name || '', args: data.args, status: 'running' as const }
              ]
            }))
          } else if (data.type === 'tool_result') {
            updateAiMessage(msg => {
              const toolCalls = (msg.toolCalls || []).map(tc =>
                tc.id === data.id || (!tc.id && tc.name === data.name && tc.status === 'running')
                  ? { ...tc, status: data.status || 'success', result: data.content }
                  : tc
              )
              return { ...msg, toolCalls }
            })
            // Insights are written back live during extraction - let the
            // source detail page refresh its insight list as they land.
            if (data.name === 'submit_insight') {
              window.dispatchEvent(new CustomEvent('onv2:insights-updated', { detail: { sourceId } }))
            }
          } else if (data.type === 'final') {
            // Final answer is the source of truth (tokens may drop mid-stream)
            if (data.content) {
              updateAiMessage(msg => ({ ...msg, content: data.content || msg.content }))
            }
          } else if (data.type === 'ai_message') {
            // Legacy complete-response event: replace content (also serves
            // as the authoritative final answer for older backend paths)
            updateAiMessage(msg => ({ ...msg, content: data.content || msg.content }))
          } else if (data.type === 'context_indicators') {
            setContextIndicators(data.data as SourceChatContextIndicator | null)
          } else if (data.type === 'error') {
            streamError = data.message || 'Stream error'
          }
        }
      }

      if (streamError) {
        throw new Error(streamError)
      }
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      console.error('Error sending message:', error)
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToSendMessage'))
      // Remove optimistic messages on error
      setMessages(prev => prev.filter(msg => !msg.id.startsWith('temp-')))
      // Drop a partially-streamed AI bubble so history stays clean
      setMessages(prev => prev.filter(msg => msg.id !== aiMessageId))
    } finally {
      // Order matters: refetch the checkpointed history FIRST, then lift the
      // streaming gate. The messages-sync effect (gated on !isStreaming)
      // then replaces the streamed bubble with the fresh history in one go -
      // flipping the gate first would briefly apply stale history.
      try {
        await refetchCurrentSession()
      } finally {
        setIsStreaming(false)
      }
    }
  }, [sourceId, currentSessionId, refetchCurrentSession, queryClient, t])

  // Cancel streaming
  const cancelStreaming = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      setIsStreaming(false)
    }
  }, [])

  // Switch session
  const switchSession = useCallback((sessionId: string) => {
    setCurrentSessionId(sessionId)
    setContextIndicators(null)
  }, [])

  // Create session
  const createSession = useCallback((data: Omit<CreateSourceChatSessionRequest, 'source_id'>) => {
    return createSessionMutation.mutate(data)
  }, [createSessionMutation])

  // Update session
  const updateSession = useCallback((sessionId: string, data: UpdateSourceChatSessionRequest) => {
    return updateSessionMutation.mutate({ sessionId, data })
  }, [updateSessionMutation])

  // Delete session
  const deleteSession = useCallback((sessionId: string) => {
    return deleteSessionMutation.mutate(sessionId)
  }, [deleteSessionMutation])

  return {
    // State
    sessions,
    currentSession: sessions.find(s => s.id === currentSessionId),
    currentSessionId,
    messages,
    isStreaming,
    contextIndicators,
    loadingSessions,
    
    // Actions
    createSession,
    updateSession,
    deleteSession,
    switchSession,
    sendMessage,
    cancelStreaming,
    refetchSessions
  }
}
