/**
 * 消息列表。
 *
 * **用户消息右对齐气泡，助手消息左对齐正文** —— 跟 Claude 桌面端一致。
 * 工具块和审批卡片挂在助手消息上面。
 *
 * **助手的正文按 Markdown 渲染**（见 {@link Markdown}）—— 模型输出的
 * `**粗体**`、列表、表格、代码块得是渲染后的样子，不是把星号露给用户看。
 * 用户消息也走同一套渲染 —— 用户贴一段代码进来时，看到的不该是裸的
 * 反引号。
 */
import Markdown from '../markdown/Markdown'
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
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      {messages.map((message) => {
        if (message.role === 'user') {
          return (
            <div key={message.id} className="mb-5 flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-tr-md border border-line bg-raised px-4 py-2.5 shadow-[0_1px_2px_oklch(0_0_0/0.05)]">
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
        return (
          <div key={message.id} className="mb-8">
            {message.toolCalls.length > 0 ? (
              <div className="mb-3 space-y-1">
                {message.toolCalls.map((call, index) => (
                  <ToolCallBlock key={call.tool_call_id ?? `${call.name}-${index}`} call={call} />
                ))}
              </div>
            ) : null}

            {message.content ? <Markdown>{message.content}</Markdown> : null}

            {!message.content && !message.toolCalls.length && busy ? (
              <div className="flex items-center gap-1.5 text-sm text-ink-faint">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ink-faint" />
                思考中…
              </div>
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
