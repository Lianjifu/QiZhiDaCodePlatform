/**
 * 聊天状态机：把后端的事件流转成消息列表。
 *
 * **事件流是唯一的事实来源** —— 后端推什么，前端就画什么：
 *
 * | 事件 | 前端动作 |
 * | --- | --- |
 * | `text` | 追加到最后一条助手消息 |
 * | `tool_call` | 在最后一条助手消息里加一个工具块（状态：进行中） |
 * | `tool_result` | 按 tool_call_id 找到那个块，补上结果 |
 * | `pending_approval` | 记下待批清单 |
 * | `error` | 这一轮炸了 —— 显示原因，别让界面停在「思考中…」 |
 * | `done` | 收尾（这一轮跑完了还是停在审批上） |
 */
import { useCallback, useRef, useState } from 'react'

import { readSSE } from '../../api/sse'
import {
  approve as approveCall,
  history,
  pendingApprovals,
  reject as rejectCall,
  streamChat,
} from '../../api/resources'
import type { AgentStreamChunk, PendingApproval, ToolCall } from '../../types/api'

/** 工具块在前端的状态。 */
export type ToolCallState = 'running' | 'succeeded' | 'failed'

/** 一次工具调用在前端的呈现。 */
export interface ToolCallView extends ToolCall {
  state: ToolCallState
  summary?: string
  /** 这次改动新增了几行（改文件的工具才有） */
  added?: number | null
  /** 这次改动删掉了几行（改文件的工具才有） */
  removed?: number | null
}

/** 一条消息。 */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  toolCalls: ToolCallView[]
  pending: PendingApproval[]
  /** 这一轮的状态（只有助手消息有） */
  status?: 'completed' | 'pending_approval'
  /** 工具消息的名字 */
  name?: string
}

let messageSeq = 0

function nextId(): string {
  messageSeq += 1
  return `m${messageSeq}`
}

/**
 * 历史消息 → 前端消息。
 *
 * **工具结果收进对应的工具块**，不单独成行 —— 流式时的观感就是这样，
 * 刷新后不该变样。工具消息按 tool_call_id 配对：配不上的（历史里被截断的）
 * 才单独留一行。
 */
function fromHistory(
  items: {
    role: string
    content: string
    name?: string | null
    tool_call_id?: string | null
    tool_calls: ToolCall[]
    added?: number | null
    removed?: number | null
  }[],
): ChatMessage[] {
  const messages: ChatMessage[] = []
  /** tool_call_id → 它在哪个消息的哪个工具块里 */
  const pending = new Map<string, { message: ChatMessage; index: number }>()

  for (const item of items) {
    if (item.role === 'tool') {
      // 工具消息：按 tool_call_id 找对应的工具块，把结果填进去
      const callId = item.tool_call_id
      const target = callId ? pending.get(callId) : undefined
      if (callId && target) {
        const call = target.message.toolCalls[target.index]
        target.message.toolCalls[target.index] = {
          ...call,
          state: 'succeeded',
          summary: item.content.slice(0, 400),
          added: item.added,
          removed: item.removed,
        }
        pending.delete(callId)
        continue
      }
      messages.push({
        id: nextId(),
        role: 'tool',
        content: item.content,
        toolCalls: [],
        pending: [],
        name: item.name ?? undefined,
      })
      continue
    }

    const message: ChatMessage = {
      id: nextId(),
      role: item.role as ChatMessage['role'],
      content: item.content,
      toolCalls: (item.tool_calls ?? []).map((call) => ({
        ...call,
        state: 'succeeded' as ToolCallState,
      })),
      pending: [],
    }
    ;(item.tool_calls ?? []).forEach((call, index) => {
      if (call.tool_call_id) pending.set(call.tool_call_id, { message, index })
    })
    messages.push(message)
  }
  return messages
}


/**
 * 把审批后的调用结果填回工具块。
 *
 * 审批期间只推了 tool_call（工具还没跑），批准后才有结果。靠 tool_call_id
 * 配对；配不上的（历史里没带 id）就按顺序兜底。
 *
 * **兜底只能用还没被领走的结果** —— 先按 id 领走一条，再按顺序兜底的话，
 * 那条已经被领走的结果会被领第二次，真正没 id 的那条反而没人领。
 */
function mergeToolResults(
  calls: ToolCallView[],
  results: { tool_call_id?: string | null; name: string; ok?: boolean | null; status?: string | null; error_code?: string | null }[],
): ToolCallView[] {
  if (results.length === 0) return calls.map((call) => ({ ...call, state: 'succeeded' }))
  const byId = new Map<string, (typeof results)[number]>()
  for (const result of results) {
    const id = result.tool_call_id ?? ''
    if (id) byId.set(id, result)
  }
  const used = new Set<(typeof results)[number]>()
  let cursor = 0
  return calls.map((call) => {
    if (call.state !== 'running') return call
    const callId = call.tool_call_id ?? ''
    let result = callId ? byId.get(callId) : undefined
    if (!result) {
      // 兜底：按顺序找一条还没被领走的结果
      while (cursor < results.length && used.has(results[cursor])) cursor += 1
      result = results[cursor]
    }
    if (!result) return call
    used.add(result)
    cursor += 1
    return {
      ...call,
      state: result.ok === false ? ('failed' as ToolCallState) : ('succeeded' as ToolCallState),
      status: result.status ?? call.status,
      error_code: result.error_code ?? call.error_code,
      ok: result.ok ?? call.ok,
    }
  })
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  /**
   * 这一轮助手消息的 id。
   *
   * **补丁要认准这条消息**，不能"最后一条助手消息" —— 切会话时历史会整个
   * 换掉，"最后一条"就成了**另一个会话**的消息，A 的流式文本会贴到 B 上。
   */
  const turnMessageIdRef = useRef<string | null>(null)

  /** 往这一轮的助手消息上打补丁。 */
  const patchTurnAssistant = useCallback((patch: (message: ChatMessage) => ChatMessage) => {
    const id = turnMessageIdRef.current
    if (!id) return
    setMessages((prev) => {
      const index = prev.findIndex((item) => item.id === id)
      if (index === -1) return prev
      const next = [...prev]
      next[index] = patch(next[index])
      return next
    })
  }, [])

  const handleEvent = useCallback(
    (event: AgentStreamChunk) => {
      switch (event.type) {
        case 'text':
          if (event.text) {
            patchTurnAssistant((message) => ({ ...message, content: message.content + event.text }))
          }
          break
        case 'tool_call':
          if (event.tool_call) {
            const call = event.tool_call
            patchTurnAssistant((message) => ({
              ...message,
              toolCalls: [
                ...message.toolCalls,
                { ...call, state: 'running' as ToolCallState },
              ],
            }))
          }
          break
        case 'tool_result': {
          const result = event.tool_result
          if (!result) break
          const resultId = result.tool_call_id ?? ''
          patchTurnAssistant((message) => {
            // 先按 id 找；两边都没 id 就取第一个"进行中"且没 id 的块
            let index = message.toolCalls.findIndex(
              (call) => resultId !== '' && (call.tool_call_id ?? '') === resultId,
            )
            if (index === -1 && resultId === '') {
              index = message.toolCalls.findIndex(
                (call) => (call.tool_call_id ?? '') === '' && call.state === 'running',
              )
            }
            if (index === -1) return message
            const toolCalls = [...message.toolCalls]
            const call = toolCalls[index]
            toolCalls[index] = {
              ...call,
              state: result.ok ? ('succeeded' as ToolCallState) : ('failed' as ToolCallState),
              summary: result.summary,
              duration_ms: result.duration_ms,
              error_code: result.error_code,
              status: result.status,
              added: result.added,
              removed: result.removed,
            }
            return { ...message, toolCalls }
          })
          break
        }
        case 'pending_approval':
          patchTurnAssistant((message) => ({ ...message, pending: event.pending }))
          break
        case 'error':
          // 后端把失败也当成一帧推出来（流已经开始，HTTP 状态码改不了了）
          if (event.error) setError(event.error)
          break
        case 'done':
          patchTurnAssistant((message) => ({
            ...message,
            status: event.status === 'pending_approval' ? 'pending_approval' : 'completed',
          }))
          break
        default:
          break
      }
    },
    [patchTurnAssistant, setError],
  )

  /** 发一条消息（流式）。 */
  const send = useCallback(
    async (text: string, threadId: string, options?: { attachments?: string[]; providerId?: number | null }) => {
      const trimmed = text.trim()
      if (!trimmed || busy) return

      setError('')
      setBusy(true)
      // 这一轮的助手消息 —— 记下它的 id，后面的事件都往它身上打补丁
      const assistantId = nextId()
      turnMessageIdRef.current = assistantId
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'user', content: trimmed, toolCalls: [], pending: [] },
        { id: assistantId, role: 'assistant', content: '', toolCalls: [], pending: [] },
      ])

      const controller = new AbortController()
      abortRef.current = controller
      try {
        const response = await streamChat(
          {
            message: trimmed,
            thread_id: threadId,
            attachments: options?.attachments ?? [],
            provider_id: options?.providerId ?? null,
          },
          controller.signal,
        )
        for await (const event of readSSE<AgentStreamChunk>(response)) {
          handleEvent(event)
        }
      } catch (cause) {
        if ((cause as Error)?.name === 'AbortError') {
          // 用户中断，不是错误
        } else {
          setError(cause instanceof Error ? cause.message : String(cause))
        }
      } finally {
        abortRef.current = null
        turnMessageIdRef.current = null
        setBusy(false)
      }
    },
    [busy, handleEvent],
  )

  /** 读会话历史。 */
  const loadHistory = useCallback(async (threadId: string) => {
    setError('')
    try {
      // 历史里没有"待批"信息 —— 那个状态在 checkpoint 的中断里，得单独问
      const [result, pending] = await Promise.all([
        history(threadId),
        pendingApprovals(threadId).catch(() => [] as PendingApproval[]),
      ])
      const messages = fromHistory(result.messages)
      if (pending.length > 0 && messages.length > 0) {
        const index = messages.length - 1
        messages[index] = {
          ...messages[index],
          pending,
          status: 'pending_approval',
        }
      }
      setMessages(messages)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [])

  /** 批准挂起的调用，图继续跑。 */
  const approve = useCallback(async (threadId: string) => {
    setBusy(true)
    setError('')
    try {
      const result = await approveCall(threadId)
      setMessages((prev) => {
        const next = [...prev]
        const index = next.findLastIndex((item) => item.role === 'assistant')
        if (index !== -1) {
          next[index] = {
            ...next[index],
            pending: [],
            content: next[index].content || result.content,
            status: 'completed',
            // 审批期间工具块一直是「进行中」—— 批准后要把结果填回同一个块
            toolCalls: mergeToolResults(next[index].toolCalls, result.tool_calls),
          }
        }
        return next
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }, [])

  /** 拒绝挂起的调用。 */
  const reject = useCallback(async (threadId: string) => {
    setBusy(true)
    setError('')
    try {
      await rejectCall(threadId)
      setMessages((prev) => {
        const next = [...prev]
        const index = next.findLastIndex((item) => item.role === 'assistant')
        if (index !== -1) {
          next[index] = { ...next[index], pending: [], status: 'completed' }
        }
        return next
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }, [])

  /** 中断当前这一轮。 */
  const abort = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  /** 清空（换新会话）。 */
  const reset = useCallback(() => {
    abortRef.current?.abort()
    setMessages([])
    setError('')
  }, [])

  return { messages, busy, error, send, loadHistory, approve, reject, abort, reset }
}
