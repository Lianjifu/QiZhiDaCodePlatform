/**
 * 消息列表。
 *
 * **用户消息右对齐气泡，助手消息左对齐纯文本** —— 跟 Claude 桌面端一致。
 * 工具块和审批卡片挂在助手消息下面。
 */
import ApprovalCard from './ApprovalCard'
import ToolCallBlock from './ToolCallBlock'
import type { ChatMessage } from './useChat'

export default function MessageList({
  messages,
  busy,
  onApprove,
  onReject,
}: {
  messages: ChatMessage[]
  busy: boolean
  onApprove: () => void
  onReject: () => void
}) {
  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-6">
      {messages.map((message) => {
        if (message.role === 'user') {
          return (
            <div key={message.id} className="mb-4 flex justify-end">
              <div className="max-w-[85%] rounded-2xl bg-raised px-4 py-2.5 text-sm whitespace-pre-wrap text-ink">
                {message.content}
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
        return (
          <div key={message.id} className="mb-6">
            {message.toolCalls.map((call, index) => (
              <ToolCallBlock key={call.tool_call_id ?? `${call.name}-${index}`} call={call} />
            ))}

            {message.content ? (
              <div className="text-sm leading-relaxed whitespace-pre-wrap text-ink">{message.content}</div>
            ) : null}

            {!message.content && !message.toolCalls.length && busy ? (
              <div className="text-sm text-ink-faint">思考中…</div>
            ) : null}

            <ApprovalCard
              pending={message.pending}
              busy={busy}
              onApprove={onApprove}
              onReject={onReject}
            />
          </div>
        )
      })}
    </div>
  )
}
