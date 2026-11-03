/**
 * 会话状态面板。
 *
 * **右侧一栏**，不是浮在侧栏上的浮层 —— 参考 Claude 桌面端的布局：
 * 面板占右侧一整列，中间是对话区。
 *
 * 内容：环境、上下文占了多少、接进来什么、产出了什么、改了多少行、现在在跑什么。
 *
 * **上下文占用是估算**（4 字符约 1 token），面板上写明了 —— 别让人拿它当
 * 精确的 token 数看。
 */
import { useEffect, useState } from 'react'

import { sessionStatus } from '../../api/resources'
import { humanSize } from '../../lib/format'
import type { SessionStatus } from '../../types/api'

/** 每块上下文占一个颜色 —— 跟参考图一样，一眼能分清谁占了多少。 */
const PART_COLORS: Record<string, string> = {
  messages: 'bg-accent',
  system_prompt: 'bg-warning',
  tools: 'bg-danger',
  mcp_tools: 'bg-success',
  skills: 'bg-ink-faint',
  memory: 'bg-ink-faint',
  autocompact: 'bg-ink-faint',
}

/** 一段内容 —— 一张独立的卡片。 */
function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-xl border border-line bg-raised px-3 py-2.5">
      <div className="mb-1.5 text-xs font-medium text-ink-muted">{title}</div>
      {children}
    </section>
  )
}

/** 可展开的明细（每个 MCP 工具、每个记忆文件）。 */
function DetailToggle({ items }: { items: string[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="ml-4 mt-0.5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="text-[11px] text-ink-faint transition-colors hover:text-ink-muted"
      >
        {open ? '▾' : '▸'} {items.length} 项
      </button>
      {open ? (
        <ul className="mt-0.5 space-y-0.5">
          {items.map((item) => (
            <li key={item} className="truncate text-[11px] text-ink-faint">
              {item}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

export default function SessionStatusPanel({ threadId }: { threadId: string }) {
  const [data, setData] = useState<SessionStatus | null>(null)
  const [loadedThread, setLoadedThread] = useState('')
  const [failure, setFailure] = useState<{ threadId: string; message: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    sessionStatus(threadId)
      .then((result) => {
        if (cancelled) return
        setData(result)
        setLoadedThread(threadId)
      })
      .catch((cause) => {
        if (cancelled) return
        setFailure({
          threadId,
          message: cause instanceof Error ? cause.message : String(cause),
        })
      })
    return () => {
      cancelled = true
    }
  }, [threadId])

  // 换会话时旧的数据不算数 —— 面板不能显示上一个会话的状态
  const status = loadedThread === threadId ? data : null
  const error = failure?.threadId === threadId ? failure.message : ''

  if (!status && !error) {
    return (
      <div className="rounded-xl border border-line bg-raised px-3 py-2.5 text-xs text-ink-faint">
        正在读会话状态…
      </div>
    )
  }

  if (error || !status) {
    return (
      <div className="rounded-xl border border-line bg-raised px-3 py-2.5 text-xs text-danger">
        读不到会话状态：{error || '未知原因'}
      </div>
    )
  }

  const { context, environment, changes } = status

  return (
    <div className="flex flex-col gap-2">
      <Section title="环境">
        <dl className="space-y-1 text-xs">
          <Row label="工作区" value={environment.workspace} mono />
          <Row label="模型" value={environment.model} />
          <Row label="供应商" value={environment.provider} />
          <Row label="信任模式" value={environment.trust_mode} />
        </dl>
      </Section>

      <Section title="上下文">
        <div className="flex items-baseline justify-between text-xs">
          <span className="text-ink">
            {context.percent}% 已用
          </span>
          <span className="text-ink-faint">
            {formatTokens(context.used)} / {formatTokens(context.limit)}
          </span>
        </div>
        {/* 分段条：每块按占比画一段 */}
        <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-sunken">
          {context.parts.map((part) => (
            <div
              key={part.key}
              className={PART_COLORS[part.key] ?? 'bg-ink-faint'}
              style={{ width: `${Math.max(part.percent, 0)}%` }}
              title={`${part.label} ${formatTokens(part.tokens)}`}
            />
          ))}
        </div>
        <ul className="mt-2 space-y-1">
          {context.parts.map((part) => (
            <li key={part.key}>
              <div className="flex items-center gap-2 text-xs">
                <span
                  className={`h-2 w-2 shrink-0 rounded-sm ${PART_COLORS[part.key] ?? 'bg-ink-faint'}`}
                />
                <span className="min-w-0 flex-1 truncate text-ink-muted">{part.label}</span>
                <span className="shrink-0 text-ink-faint">{formatTokens(part.tokens)}</span>
                <span className="w-10 shrink-0 text-right text-ink-faint">{part.percent}%</span>
              </div>
              {/* 有明细的可以展开看看具体是什么 */}
              {part.items && part.items.length > 0 ? (
                <DetailToggle items={part.items} />
              ) : null}
            </li>
          ))}
          <li className="flex items-center gap-2 text-xs">
            <span className="h-2 w-2 shrink-0 rounded-sm bg-transparent" />
            <span className="min-w-0 flex-1 truncate text-ink-muted">剩余空间</span>
            <span className="shrink-0 text-ink-faint">{formatTokens(context.free)}</span>
            <span className="w-10 shrink-0 text-right text-ink-faint">
              {Math.max(100 - context.percent, 0).toFixed(1)}%
            </span>
          </li>
        </ul>
        <p className="mt-2 text-[10px] leading-relaxed text-ink-faint">
          按 4 字符约 1 token 估算，不是精确的 token 数。
        </p>
      </Section>

      {status.connectors.length > 0 && (
        <Section title="连接">
          <ul className="space-y-1">
            {status.connectors.map((connector) => (
              <li key={`${connector.kind}-${connector.name}`} className="flex items-center gap-2 text-xs">
                <span
                  className={
                    connector.status === 'ok' ? 'text-success' : 'text-ink-faint'
                  }
                >
                  {connector.status === 'ok' ? '●' : '○'}
                </span>
                <span className="min-w-0 flex-1 truncate text-ink">{connector.name}</span>
                <span className="shrink-0 text-ink-faint">
                  {connector.kind === 'mcp' ? 'MCP' : '模型'}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="产出">
        {status.outputs.length === 0 ? (
          <div className="text-xs text-ink-faint">还没有文件</div>
        ) : (
          <ul className="space-y-1">
            {status.outputs.slice(0, 8).map((file) => (
              <li key={file.path} className="flex items-center gap-2 text-xs">
                <span className="min-w-0 flex-1 truncate text-ink">{file.path}</span>
                <span className="shrink-0 text-ink-faint">
                  {file.is_dir ? '目录' : humanSize(file.size)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {status.outputs.length > 8 ? (
          <div className="mt-1 text-[11px] text-ink-faint">
            还有 {status.outputs.length - 8} 个…
          </div>
        ) : null}
      </Section>

      <Section title="改动">
        {changes.added || changes.removed ? (
          <div className="font-mono text-sm">
            {changes.added ? <span className="text-success">+{changes.added}</span> : null}
            {changes.removed ? (
              <span className={changes.added ? 'ml-2 text-danger' : 'text-danger'}>
                −{changes.removed}
              </span>
            ) : null}
            <span className="ml-2 text-[11px] text-ink-faint">行</span>
          </div>
        ) : (
          <div className="text-xs text-ink-faint">这个会话还没改过文件</div>
        )}
      </Section>

      <Section title="后台任务">
        {status.tasks.length === 0 ? (
          <div className="text-xs text-ink-faint">现在没有在跑的任务</div>
        ) : (
          <ul className="space-y-1">
            {status.tasks.map((task, index) => (
              <li key={`${task.tool}-${index}`} className="flex items-center gap-2 text-xs">
                <span className="min-w-0 flex-1 truncate text-ink">
                  <span className="text-ink-muted">{task.tool}</span>
                  {task.detail ? ` ${task.detail}` : ''}
                </span>
                <span className="shrink-0 text-ink-faint">{formatDuration(task.elapsed_ms)}</span>
              </li>
            ))}
          </ul>
        )}
        {status.finished_tasks > 0 ? (
          <div className="mt-1 text-[11px] text-ink-faint">
            {status.finished_tasks} 个已完成
          </div>
        ) : null}
      </Section>

      <div className="px-3 py-1 text-[10px] leading-relaxed text-ink-faint">
        最后更新：{formatUpdatedAt(status.updated_at)}。每 30 秒刷新一次。
      </div>
    </div>
  )
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="w-14 shrink-0 text-ink-faint">{label}</dt>
      <dd
        className={
          mono
            ? 'min-w-0 flex-1 truncate font-mono text-[11px] text-ink-muted'
            : 'min-w-0 flex-1 truncate text-ink-muted'
        }
        title={value}
      >
        {value || '—'}
      </dd>
    </div>
  )
}

/** token 数显示成 k —— 面板上写「128k」比「128000」好读。 */
function formatTokens(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return String(value)
}

/** 毫秒显示成「2h 53m」那样。 */
function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

/** 最后更新 —— 相对时间。 */
function formatUpdatedAt(value: string): string {
  if (!value) return '刚刚'
  const stamp = Date.parse(value)
  if (Number.isNaN(stamp)) return '刚刚'
  const seconds = Math.floor((Date.now() - stamp) / 1000)
  if (seconds < 10) return '刚刚'
  if (seconds < 60) return `${seconds} 秒前`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  return `${Math.floor(minutes / 60)} 小时前`
}
