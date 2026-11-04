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

import { readSSE, StreamInterruptedError } from '../../api/sse'
import {
  approve as approveCall,
  forkConversation,
  history,
  pendingApprovals,
  regenerateChat,
  reject as rejectCall,
  streamChat,
} from '../../api/resources'
import type { AgentStreamChunk, PendingApproval, ToolCall } from '../../types/api'
import { isFailedResult } from './toolSummary'

/**
 * 工具块在前端的状态。
 *
 * `pending` 是**历史回填专用** —— 历史里没有结果的工具调用说明这一轮没跑完
 * （在等审批，或者上次跑到一半就断了）。标成 `running` 的话刷新一次就永远
 * 在转圈，看着像还活着。
 */
export type ToolCallState = 'running' | 'succeeded' | 'failed' | 'pending'

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
  /**
   * 审批的处置结果。
   *
   * **批准 / 拒绝之后要立刻有个交代** —— 后端 resume 是同步跑完整张图的，
   * 可能几十秒，卡片只是变灰的话用户会以为页面卡死了。
   */
  pendingDecision?: 'approved' | 'rejected' | null
  /** 工具消息的名字 */
  name?: string
  /** checkpoint 里的消息 id —— 分支会话靠它定位分支点（流式产生的消息没有） */
  sourceId?: string | null
}

let messageSeq = 0

function nextId(): string {
  messageSeq += 1
  return `m${messageSeq}`
}

/** 一个还没配上结果的工具块（历史回填时用）。 */
interface UnresolvedCall {
  message: ChatMessage
  index: number
  name: string
  id?: string | null
}

/**
 * 历史消息 → 前端消息。
 *
 * **工具结果收进对应的工具块**，不单独成行 —— 流式时的观感就是这样，
 * 刷新后不该变样。工具消息按 tool_call_id 配对：**配不上的按顺序兜底**，
 * 再配不上也并进工具块 —— 退化成一行纯文本会让用户以为 agent 调了个
 * 没名字的工具。
 */
export function fromHistory(
  items: {
    id?: string | null
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
  /** tool_call_id → 它对应的那个工具块 */
  const pending = new Map<string, UnresolvedCall>()
  /** 还没配上结果的工具块，按出现顺序 —— 用来兜底配对 */
  const unresolved: UnresolvedCall[] = []

  for (const item of items) {
    if (item.role === 'tool') {
      // 工具消息：先按 tool_call_id 找，找不到就按名字、再按顺序兜底
      const callId = item.tool_call_id ?? undefined
      let target = callId ? pending.get(callId) : undefined
      if (!target) {
        target = unresolved.find((entry) => entry.name === item.name)
      }
      if (!target) {
        target = unresolved[0]
      }

      const failed = isFailedResult(item.content, item.name ?? undefined)
      const summary = item.content.slice(0, 400)

      if (target) {
        const call = target.message.toolCalls[target.index]
        target.message.toolCalls[target.index] = {
          ...call,
          state: failed ? 'failed' : 'succeeded',
          summary,
          added: item.added,
          removed: item.removed,
        }
        pending.delete(target.id ?? '')
        const at = unresolved.indexOf(target)
        if (at !== -1) unresolved.splice(at, 1)
        continue
      }

      // **连工具块都没有** —— 把结果并进最后一条助手消息的工具块列表，
      // 别退化成一行没有上下文的纯文本。
      const host = messages[messages.length - 1]
      if (host && host.role === 'assistant') {
        host.toolCalls = [
          ...host.toolCalls,
          {
            name: item.name ?? 'tool',
            args: {},
            state: failed ? 'failed' : 'succeeded',
            summary,
            added: item.added,
            removed: item.removed,
          },
        ]
        continue
      }

      // 历史里一条助手消息都没有 —— 只能单独留一行
      messages.push({
        id: nextId(),
        role: 'tool',
        content: item.content,
        toolCalls: [],
        pending: [],
        name: item.name ?? undefined,
        sourceId: item.id ?? null,
      })
      continue
    }

    const message: ChatMessage = {
      id: nextId(),
      role: item.role as ChatMessage['role'],
      content: item.content,
      sourceId: item.id ?? null,
      // 先当"没结果"：结果消息到了才改成成功/失败。
      // **标成 running 是骗人** —— 刷新之后它会永远转圈。
      toolCalls: (item.tool_calls ?? []).map((call) => ({
        ...call,
        state: 'pending' as ToolCallState,
      })),
      pending: [],
    }
    ;(item.tool_calls ?? []).forEach((call, index) => {
      const entry: UnresolvedCall = {
        message,
        index,
        name: call.name,
        id: call.tool_call_id,
      }
      unresolved.push(entry)
      if (call.tool_call_id) pending.set(call.tool_call_id, entry)
    })
    messages.push(message)
  }
  return messages
}

/** 一轮对话 —— 一次发消息或一次重新生成。 */
interface Turn {
  /**
   * 轮次代号：每轮 +1。
   *
   * **所有补丁与收尾都要先比对代号** —— 用户「停止」之后立刻再发一条时，
   * 旧轮的收尾会把新轮的控制器与消息 id 一起抹掉，新轮的内容就全丢了。
   */
  token: number
  /** 这一轮助手消息的 id */
  messageId: string
  controller: AbortController
  /** 用户主动中断了 */
  aborted: boolean
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  /** 历史正在加载 —— 空会话页要靠它区分「没内容」和「还没加载完」 */
  const [loading, setLoading] = useState(false)
  /** 当前这一轮 */
  const currentTurnRef = useRef<Turn | null>(null)
  /** 轮次计数器 */
  const turnSeqRef = useRef(0)
  /**
   * busy 的即时镜像。
   *
   * **state 更新要等一次渲染** —— 用户「停止」之后立刻再发一条时，
   * 组件里的 busy 还是 true，消息就被吞了。
   */
  const busyRef = useRef(false)

  /** 往某条消息上打补丁 —— **不看轮次**，收尾时用。 */
  const patchMessage = useCallback(
    (messageId: string, patch: (message: ChatMessage) => ChatMessage) => {
      setMessages((prev) => {
        const index = prev.findIndex((item) => item.id === messageId)
        if (index === -1) return prev
        const next = [...prev]
        next[index] = patch(next[index])
        return next
      })
    },
    [],
  )

  /** 往这一轮的助手消息上打补丁。 */
  const patchTurnAssistant = useCallback(
    (turn: Turn, patch: (message: ChatMessage) => ChatMessage) => {
      // **补丁要认准轮次** —— 旧轮晚到一步的帧贴到新轮的消息上，
      // 新轮的助手气泡里就会混进上一轮的文本。
      if (currentTurnRef.current?.token !== turn.token) return
      patchMessage(turn.messageId, patch)
    },
    [patchMessage],
  )

  const handleEvent = useCallback(
    (turn: Turn, event: AgentStreamChunk) => {
      switch (event.type) {
        case 'text':
          if (event.text) {
            patchTurnAssistant(turn, (message) => ({
              ...message,
              content: message.content + event.text,
            }))
          }
          break
        case 'tool_call':
          if (event.tool_call) {
            const call = event.tool_call
            patchTurnAssistant(turn, (message) => ({
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
          patchTurnAssistant(turn, (message) => {
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
          patchTurnAssistant(turn, (message) => ({ ...message, pending: event.pending }))
          break
        case 'error':
          // 后端把失败也当成一帧推出来（流已经开始，HTTP 状态码改不了了）
          // 旧轮的错误别报到新轮的界面上
          if (event.error && currentTurnRef.current?.token === turn.token) setError(event.error)
          break
        case 'done':
          patchTurnAssistant(turn, (message) => ({
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

  /**
   * 把还在转圈的工具块收尾。
   *
   * **工具块不能永远转圈** —— 流断了或者用户叫停了，都不会再有结果帧过来，
   * 留一个永远不结束的进度条比标成失败更让人困惑。
   */
  const settleRunningTools = useCallback(
    (turn: Turn, state: ToolCallState) => {
      // **直接按消息 id 打** —— 这一轮可能已经不是当前轮了（用户叫停后
      // 立刻又发了一条），但它自己的工具块照样得停下来。
      patchMessage(turn.messageId, (message) => ({
        ...message,
        toolCalls: message.toolCalls.map((call) =>
          call.state === 'running' ? { ...call, state } : call,
        ),
      }))
    },
    [patchMessage],
  )

  /** 发一条消息（流式）。 */
  /** 把一轮的 SSE 帧读掉，事件往当前助手消息上打补丁。 */
  const consume = useCallback(
    async (turn: Turn, response: Response) => {
      try {
        for await (const event of readSSE<AgentStreamChunk>(response)) {
          handleEvent(turn, event)
        }
      } catch (cause) {
        // 用户中断 —— 不是错误，交给上层收尾
        if ((cause as Error)?.name === 'AbortError') throw cause
        // 旧轮的失败不该报到新轮的界面上
        if (currentTurnRef.current?.token !== turn.token) return
        if (turn.aborted) {
          // 用户叫停的 —— 别报「连接中断」，但工具块别永远转圈
          settleRunningTools(turn, 'pending')
          return
        }
        // **断流不是正常收尾** —— 后端在 done 帧之前就断了（代理超时、进程被杀）。
        setError(
          cause instanceof StreamInterruptedError
            ? '连接中断'
            : cause instanceof Error
              ? cause.message
              : String(cause),
        )
        settleRunningTools(turn, 'failed')
      }
    },
    [handleEvent, settleRunningTools, setError],
  )

  /** 开一轮新的对话。 */
  const startTurn = useCallback((messageId: string): Turn => {
    const turn: Turn = {
      token: ++turnSeqRef.current,
      messageId,
      controller: new AbortController(),
      aborted: false,
    }
    currentTurnRef.current = turn
    busyRef.current = true
    setBusy(true)
    return turn
  }, [])

  /** 一轮收尾 —— **只有当前轮能收**，否则会把新一轮的状态一起抹掉。 */
  const finishTurn = useCallback((turn: Turn) => {
    if (currentTurnRef.current?.token !== turn.token) return
    currentTurnRef.current = null
    busyRef.current = false
    setBusy(false)
  }, [])

  const send = useCallback(
    async (
      text: string,
      threadId: string,
      options?: { attachments?: string[]; providerId?: number | null },
    ) => {
      const trimmed = text.trim()
      if (!trimmed || busyRef.current) return

      setError('')
      // 这一轮的助手消息 —— 记下它的 id，后面的事件都往它身上打补丁
      const assistantId = nextId()
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'user', content: trimmed, toolCalls: [], pending: [] },
        { id: assistantId, role: 'assistant', content: '', toolCalls: [], pending: [] },
      ])
      const turn = startTurn(assistantId)

      try {
        const response = await streamChat(
          {
            message: trimmed,
            thread_id: threadId,
            attachments: options?.attachments ?? [],
            provider_id: options?.providerId ?? null,
          },
          turn.controller.signal,
        )
        await consume(turn, response)
      } catch (cause) {
        if ((cause as Error)?.name === 'AbortError') {
          // 用户中断，不是错误 —— 但工具块不能留个永远转圈的进度条
          settleRunningTools(turn, 'pending')
        } else {
          setError(cause instanceof Error ? cause.message : String(cause))
        }
      } finally {
        finishTurn(turn)
      }
    },
    [consume, finishTurn, settleRunningTools, startTurn],
  )

  /**
   * 重新生成上一轮。
   *
   * 后端把最后一条用户消息起往后全删再重跑；**前端也得跟着删** ——
   * 不删的话屏幕上会留着旧回复，新回复又追加在后面，看着像两轮。
   */
  const regenerate = useCallback(
    async (threadId: string) => {
      if (busyRef.current) return
      setError('')
      const assistantId = nextId()
      setMessages((prev) => {
        const index = prev.findLastIndex((item) => item.role === 'user')
        // **保留到用户消息（含）** —— 后端是拿那条用户消息重跑的，它还在
        // 历史里；前端要是把它也删了，刷新一下就又冒出来了。
        const kept = index === -1 ? prev : prev.slice(0, index + 1)
        return [...kept, { id: assistantId, role: 'assistant', content: '', toolCalls: [], pending: [] }]
      })
      const turn = startTurn(assistantId)

      try {
        await consume(turn, await regenerateChat(threadId, {}, turn.controller.signal))
      } catch (cause) {
        if ((cause as Error)?.name !== 'AbortError') {
          setError(cause instanceof Error ? cause.message : String(cause))
        } else {
          // 用户中断 —— 工具块别留个永远转圈的进度条
          settleRunningTools(turn, 'pending')
        }
      } finally {
        finishTurn(turn)
      }
    },
    [consume, finishTurn, settleRunningTools, startTurn],
  )

  /**
   * 从某条消息分支出新会话，返回新会话 id（失败返回 null）。
   *
   * **分支点用消息自己的 checkpoint id** —— 早先传的是"渲染数组的第几条"，
   * 再去重新拉的历史里按同一下标找：本轮还在流的消息根本不在历史里，
   * 两个数组长度与顺序对不上，分支点会悄悄错位，用户毫无察觉。
   */
  const forkAt = useCallback(
    async (threadId: string, sourceId: string | null): Promise<string | null> => {
      setError('')
      if (!sourceId) {
        setError('这一条还没进历史，没法分支')
        return null
      }
      try {
        const forked = await forkConversation(threadId, sourceId)
        return forked.thread_id
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
        return null
      }
    },
    [],
  )

  /** 读会话历史。 */
  const loadHistory = useCallback(async (threadId: string) => {
    setError('')
    setLoading(true)
    try {
      // 历史里没有"待批"信息 —— 那个状态在 checkpoint 的中断里，得单独问
      const [result, pending] = await Promise.all([
        history(threadId),
        pendingApprovals(threadId).catch(() => [] as PendingApproval[]),
      ])
      const messages = fromHistory(result.messages)
      if (pending.length > 0) {
        // 待批挂在**最后一条助手消息**上 —— 审批卡片是助手消息的一部分。
        // 挂到"最后一条消息"是不行的：最后一条可能是工具消息，卡片就不见了。
        const index = messages.findLastIndex((item) => item.role === 'assistant')
        if (index !== -1) {
          messages[index] = {
            ...messages[index],
            pending,
            status: 'pending_approval',
          }
        }
      }
      setMessages(messages)
    } catch (cause) {
      // **加载失败也要清屏** —— 否则上一个会话的消息会挂在新会话的 URL 下，
      // 用户以为在看新会话，看见的却是上一段对话。
      setMessages([])
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  /** 记下审批的处置结果（批准 / 拒绝）。 */
  const setPendingDecision = useCallback((decision: 'approved' | 'rejected' | null) => {
    setMessages((prev) => {
      const index = prev.findLastIndex(
        (item) => item.role === 'assistant' && item.pending.length > 0,
      )
      if (index === -1) return prev
      const next = [...prev]
      next[index] = { ...next[index], pendingDecision: decision }
      return next
    })
  }, [])

  /**
   * 批准挂起的调用，图继续跑。
   *
   * `decisions` 逐条给决定（一条挂起的调用一个，顺序与待批清单一致）——
   * 不给就是全部批准。
   */
  const approve = useCallback(
    async (threadId: string, decisions?: ('approve' | 'reject')[]) => {
      // **busy 与 busyRef 一起改** —— 审批是长请求，期间不能发新消息，
      // 否则新一轮的 busy 会被这里的收尾抹掉。
      busyRef.current = true
      setBusy(true)
      setError('')
      // **先给个交代** —— 后端 resume 是同步跑完整张图的，可能几十秒。
      // 卡片只是变灰的话，用户会以为页面卡死了。
      setPendingDecision('approved')
      try {
        await approveCall(threadId, decisions)
        // 图恢复后 agent 会接着往下跑，**可能又撞上新的审批**（批准一张卡不等于
        // 这一轮结束）。只把当前这张卡关掉的话，新的待批就丢了 —— 页面看着像
        // 卡住了。重读历史与待批清单，让新的卡片出来。
        await loadHistory(threadId)
      } catch (cause) {
        setPendingDecision(null)
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        busyRef.current = false
        setBusy(false)
      }
    },
    [loadHistory, setPendingDecision],
  )

  /** 拒绝挂起的调用。 */
  const reject = useCallback(
    async (threadId: string, decisions?: ('approve' | 'reject')[]) => {
      busyRef.current = true
      setBusy(true)
      setError('')
      setPendingDecision('rejected')
      try {
        await rejectCall(threadId, decisions)
        // 同 approve：恢复之后可能又撞上新的审批。
        await loadHistory(threadId)
      } catch (cause) {
        setPendingDecision(null)
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        busyRef.current = false
        setBusy(false)
      }
    },
    [loadHistory, setPendingDecision],
  )

  /** 中断当前这一轮。 */
  const abort = useCallback(() => {
    const turn = currentTurnRef.current
    if (!turn) return
    turn.aborted = true
    turn.controller.abort()
    // **中断后立刻回到可输入状态** —— 后端把流关掉可能要一会儿，
    // 用户干等着就发不出下一条了。
    busyRef.current = false
    setBusy(false)
  }, [])

  /** 清空（换新会话）。 */
  const reset = useCallback(() => {
    abort()
    setMessages([])
    setError('')
  }, [abort])

  return {
    messages,
    busy,
    error,
    loading,
    send,
    regenerate,
    forkAt,
    loadHistory,
    approve,
    reject,
    abort,
    reset,
  }
}
