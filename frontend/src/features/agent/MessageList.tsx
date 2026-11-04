/**
 * 消息列表。
 *
 * **用户消息右对齐气泡，助手消息左对齐正文** —— 跟 Claude 桌面端一致。
 * 工具块和审批卡片挂在助手消息上面。
 *
 * **助手和用户的正文都按 Markdown 渲染**（见 {@link Markdown}）—— 模型输出的
 * `**粗体**`、列表、表格、代码块得是渲染后的样子，不是把星号露给用户看。
 *
 * **助手消息右下角有操作条**（悬停出现）：复制 / 重新生成 / 分支。
 *
 * ## 为什么要 memo 每一行
 *
 * 流式输出时每来一个字，整个列表都会重渲染 —— 历史消息也跟着重渲染一遍，
 * 几百条消息时Markdown 解析会把界面卡住。历史消息的内容**不变**，所以每行
 * 都用 `memo` 包起来：只有真正变了的那一行才重渲染。
 *
 * ## 长会话只渲染一段
 *
 * 几百条消息全渲染，DOM 节点数也跟着涨。这里只渲染**最近的一段**（默认 40
 * 条），往上滚可以再加载更早的 —— 消息还在，只是不在 DOM 里。
 */
import { memo, useState } from 'react'

import { BranchIcon, CopyIcon, RefreshIcon } from '../../components/icons'
import Markdown from '../markdown/Markdown'
import ApprovalCard, { type ApprovalDecision } from './ApprovalCard'
import { messageWindow } from './messageWindow'
import ToolCallBlock from './ToolCallBlock'
import type { ChatMessage } from './useChat'

export default function MessageList({
  messages,
  busy,
  visibleCount,
  onLoadEarlier,
  onApprove,
  onReject,
  onRegenerate,
  onFork,
}: {
  messages: ChatMessage[]
  busy: boolean
  /** 渲染最近多少条 */
  visibleCount: number
  /** 往上再加载一批 */
  onLoadEarlier: () => void
  onApprove: (decisions?: ApprovalDecision[]) => void
  onReject: (decisions?: ApprovalDecision[]) => void
  /** 重新生成上一轮 */
  onRegenerate: () => void
  /** 从这条消息分支出新会话 —— 传它自己的 checkpoint id */
  onFork: (sourceId: string | null) => void
}) {
  // **只渲染最近的一段** —— 更早的消息点一下再加载
  const { shown, hidden } = messageWindow(messages, visibleCount)
  const lastIndex = shown.length - 1

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      {hidden > 0 ? (
        <button
          type="button"
          onClick={onLoadEarlier}
          className="mb-6 w-full rounded-xl border border-line bg-sunken/60 py-2 text-xs text-ink-muted transition-colors hover:border-accent/40 hover:text-ink"
        >
          还有 {hidden} 条更早的消息 —— 点这里加载
        </button>
      ) : null}

      {shown.map((message, index) => (
        <MessageRow
          key={message.id}
          message={message}
          busy={busy}
          streaming={busy && index === lastIndex}
          onApprove={onApprove}
          onReject={onReject}
          onRegenerate={onRegenerate}
          onFork={onFork}
        />
      ))}
    </div>
  )
}

/**
 * 一条消息。
 *
 * **memo 是这里的关键** —— 消息对象没变就不重渲染（流式时只有最后一条在变）。
 */
const MessageRow = memo(function MessageRow({
  message,
  busy,
  streaming,
  onApprove,
  onReject,
  onRegenerate,
  onFork,
}: {
  message: ChatMessage
  busy: boolean
  streaming: boolean
  onApprove: (decisions?: ApprovalDecision[]) => void
  onReject: (decisions?: ApprovalDecision[]) => void
  onRegenerate: () => void
  onFork: (sourceId: string | null) => void
}) {
  if (message.role === 'user') {
    return (
      <div className="group mb-6 flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-tr-md border border-accent/25 bg-accent-soft/70 px-4 py-2.5 shadow-1">
          <Markdown>{message.content}</Markdown>
        </div>
      </div>
    )
  }

  if (message.role === 'tool') {
    return (
      <div className="mb-2 text-xs text-ink-faint">
        <span className="font-mono">{message.name}</span> 返回：
        <span className="ml-1 text-ink-muted">{message.content.slice(0, 200)}</span>
      </div>
    )
  }

  // 助手消息
  return (
    <div className="group mb-8">
      {message.toolCalls.length > 0 ? (
        <div className="mb-3 space-y-1">
          {message.toolCalls.map((call, callIndex) => (
            <ToolCallBlock key={call.tool_call_id ?? `${call.name}-${callIndex}`} call={call} />
          ))}
        </div>
      ) : null}

      {message.content ? (
        <div className={streaming ? 'stream-cursor' : undefined}>
          <Markdown>{message.content}</Markdown>
        </div>
      ) : null}

      {!message.content && !message.toolCalls.length && busy ? (
        <div className="flex items-center gap-1 text-sm text-ink-faint">
          <span className="thinking-dot h-1.5 w-1.5 rounded-full bg-current" />
          <span className="thinking-dot h-1.5 w-1.5 rounded-full bg-current" />
          <span className="thinking-dot h-1.5 w-1.5 rounded-full bg-current" />
          <span className="ml-1">思考中</span>
        </div>
      ) : null}

      <ApprovalCard
        pending={message.pending}
        busy={busy}
        decision={message.pendingDecision}
        onApprove={onApprove}
        onReject={onReject}
      />

      {message.content && !streaming ? (
        <MessageActions
          content={message.content}
          onRegenerate={onRegenerate}
          onFork={() => onFork(message.sourceId ?? null)}
        />
      ) : null}
    </div>
  )
})

/** 助手消息的操作条 —— 悬停才出现。 */
const MessageActions = memo(function MessageActions({
  content,
  onRegenerate,
  onFork,
}: {
  content: string
  onRegenerate: () => void
  onFork: () => void
}) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // 剪贴板不可用（非 https / 没授权）—— 不弹错
    }
  }

  return (
    <div className="mt-2 flex items-center justify-end gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
      <ActionButton title="复制回复" onClick={() => void copy()}>
        {copied ? <span className="text-[10px]">已复制</span> : <CopyIcon className="h-3.5 w-3.5" />}
      </ActionButton>
      <ActionButton title="重新生成" onClick={onRegenerate}>
        <RefreshIcon className="h-3.5 w-3.5" />
      </ActionButton>
      <ActionButton title="从这里分支" onClick={onFork}>
        <BranchIcon className="h-3.5 w-3.5" />
      </ActionButton>
    </div>
  )
})

function ActionButton({
  title,
  onClick,
  children,
}: {
  title: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="flex h-7 min-w-7 items-center justify-center rounded-lg px-1.5 text-ink-faint transition-colors hover:bg-hover hover:text-ink-muted"
    >
      {children}
    </button>
  )
}
