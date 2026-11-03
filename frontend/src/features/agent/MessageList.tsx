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
 */
import { useState } from 'react'

import { BranchIcon, CopyIcon, RefreshIcon } from '../../components/icons'
import Markdown from '../markdown/Markdown'
import ApprovalCard from './ApprovalCard'
import ToolCallBlock from './ToolCallBlock'
import type { ChatMessage } from './useChat'

export default function MessageList({
  messages,
  busy,
  onApprove,
  onReject,
  onRegenerate,
  onFork,
}: {
  messages: ChatMessage[]
  busy: boolean
  onApprove: () => void
  onReject: () => void
  /** 重新生成上一轮 */
  onRegenerate: () => void
  /** 从第 index 条消息分支出新会话 */
  onFork: (index: number) => void
}) {
  const lastIndex = messages.length - 1

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      {messages.map((message, index) => {
        if (message.role === 'user') {
          return (
            <div key={message.id} className="group mb-6 flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-tr-md border border-accent/25 bg-accent-soft/70 px-4 py-2.5 shadow-1">
                <Markdown>{message.content}</Markdown>
              </div>
            </div>
          )
        }

        if (message.role === 'tool') {
          return (
            <div key={message.id} className="mb-2 text-xs text-ink-faint">
              <span className="font-mono">{message.name}</span> 返回：
              <span className="ml-1 text-ink-muted">{message.content.slice(0, 200)}</span>
            </div>
          )
        }

        // 助手消息
        const streaming = busy && index === lastIndex
        return (
          <div key={message.id} className="group mb-8">
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
              onApprove={onApprove}
              onReject={onReject}
            />

            {message.content && !streaming ? (
              <MessageActions
                content={message.content}
                onRegenerate={onRegenerate}
                onFork={() => onFork(index)}
              />
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

/** 助手消息的操作条 —— 悬停才出现。 */
function MessageActions({
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
}

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
