/**
 * 工具调用卡片。
 *
 * 折叠时一行：**图标 + 人话 + 目标 + 状态**；展开才给参数与结果。
 *
 * 设计上刻意避开"两大块灰 JSON"那种老气样式：
 *
 * - **参数逐行列出**（标量一行一个），结构化的值单独用代码块；
 * - **结果**是一个带边框的代码面板，不是灰底大块；
 * - 状态用一个小圆点 + 图标表达，不用大块的彩色底。
 *
 * 结果摘要后端已经截断了 —— 工具输出可能有几万 token，原样塞进来会撑爆页面。
 */
import { useState } from 'react'

import {
  CheckIcon,
  ChevronIcon,
  FileIcon,
  PencilIcon,
  SearchIcon,
  TerminalIcon,
} from '../../components/icons'
import type { ToolCallView } from './useChat'
import { toolArgRows, toolLabel, toolTarget } from './toolSummary'

/** 工具名 → 图标。 */
function iconFor(name: string) {
  if (name === 'execute' || name === 'run_python') return <TerminalIcon className="h-3.5 w-3.5" />
  if (name === 'grep' || name === 'glob' || name === 'web_search') {
    return <SearchIcon className="h-3.5 w-3.5" />
  }
  if (name === 'write_file' || name === 'edit_file') return <PencilIcon className="h-3.5 w-3.5" />
  return <FileIcon className="h-3.5 w-3.5" />
}

export default function ToolCallBlock({ call }: { call: ToolCallView }) {
  const [open, setOpen] = useState(false)

  const label = toolLabel(call.name)
  const target = toolTarget(call.args ?? {})
  const rows = toolArgRows(call.args ?? {})
  const failed = call.state === 'failed'

  // 改动量：改过文件的工具才有（+12 −3）
  const hasDiff = typeof call.added === 'number' || typeof call.removed === 'number'

  return (
    <div className="group">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={[
          'flex w-full items-center gap-2 rounded-lg border px-2 py-1.5 text-left transition-colors',
          open
            ? 'border-line bg-sunken/70'
            : 'border-transparent hover:border-line hover:bg-sunken/50',
        ].join(' ')}
      >
        <span
          className={[
            'flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors',
            failed
              ? 'border-danger/30 bg-danger-soft text-danger'
              : 'border-line bg-base text-ink-faint',
          ].join(' ')}
        >
          {iconFor(call.name)}
        </span>

        <span className="shrink-0 text-xs text-ink">{label}</span>

        {target ? (
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-muted">
            {target}
          </span>
        ) : (
          <span className="flex-1" />
        )}

        {hasDiff ? (
          <span className="shrink-0 font-mono text-[11px]">
            {call.added ? <span className="text-success">+{call.added}</span> : null}
            {call.removed ? <span className="ml-1 text-danger">−{call.removed}</span> : null}
          </span>
        ) : null}

        {call.duration_ms ? (
          <span className="shrink-0 text-[11px] text-ink-faint">
            {(call.duration_ms / 1000).toFixed(1)}s
          </span>
        ) : null}

        <span className="flex h-4 w-4 shrink-0 items-center justify-center">
          {call.state === 'running' ? (
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
          ) : failed ? (
            <span className="h-1.5 w-1.5 rounded-full bg-danger" />
          ) : (
            <CheckIcon className="h-3.5 w-3.5 text-success" />
          )}
        </span>

        <ChevronIcon
          className={[
            'h-3.5 w-3.5 shrink-0 text-ink-faint transition-transform',
            open ? 'rotate-90' : '',
          ].join(' ')}
        />
      </button>

      {open ? (
        <div className="mt-1 space-y-2 rounded-lg border border-line bg-sunken/50 p-2.5">
          {rows.length > 0 ? (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint">
                参数
              </div>
              <dl className="space-y-1">
                {rows.map((row) => (
                  <div key={row.key} className="flex gap-2 text-[11px]">
                    <dt className="w-20 shrink-0 truncate font-mono text-ink-faint">{row.key}</dt>
                    <dd
                      className={[
                        'min-w-0 flex-1 break-all',
                        row.structured ? 'font-mono text-ink-muted' : 'text-ink-muted',
                      ].join(' ')}
                    >
                      {row.value}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}

          {call.summary ? (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint">
                结果
              </div>
              <pre
                className={[
                  'max-h-56 overflow-auto whitespace-pre-wrap rounded-md border px-2 py-1.5',
                  'font-mono text-[11px] leading-relaxed',
                  failed
                    ? 'border-danger/30 bg-danger-soft/40 text-danger'
                    : 'border-line bg-base text-ink-muted',
                ].join(' ')}
              >
                {call.summary}
              </pre>
            </div>
          ) : null}

          {call.error_code ? (
            <div className="text-[11px] text-danger">错误码：{call.error_code}</div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
