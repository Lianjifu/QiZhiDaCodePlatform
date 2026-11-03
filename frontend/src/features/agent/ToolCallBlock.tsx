/**
 * 工具调用折叠块。
 *
 * 长得像 Claude 那样：**一行摘要 + 展开看细节**。折叠时只显示
 * 「Ran xxx 1.2s ✓」，展开才给参数与结果摘要。
 *
 * 结果摘要后端已经截断了 —— 工具输出可能有几万 token，原样塞进来会撑爆页面。
 */
import { useState } from 'react'

import {
  CheckIcon,
  FileIcon,
  PencilIcon,
  SearchIcon,
  TerminalIcon,
} from '../../components/icons'
import type { ToolCallView } from './useChat'

/** 工具名 → 人话。深Agents 的工具名是 snake_case。 */
const TOOL_LABELS: Record<string, string> = {
  read_file: '读取文件',
  write_file: '写入文件',
  edit_file: '编辑文件',
  delete: '删除文件',
  ls: '列出目录',
  glob: '按名字找文件',
  grep: '搜索内容',
  execute: '执行命令',
  run_python: '运行 Python',
  todo_write: '更新任务清单',
  task: '调用子代理',
}

/** 工具名 → 图标。 */
function iconFor(name: string) {
  if (name === 'execute' || name === 'run_python') return <TerminalIcon className="h-3.5 w-3.5" />
  if (name === 'grep' || name === 'glob') return <SearchIcon className="h-3.5 w-3.5" />
  if (name === 'write_file' || name === 'edit_file') return <PencilIcon className="h-3.5 w-3.5" />
  return <FileIcon className="h-3.5 w-3.5" />
}

/** 工具名 + 参数 → 一行摘要。 */
function summarize(call: ToolCallView): string {
  const label = TOOL_LABELS[call.name] ?? call.name
  const args = call.args ?? {}
  // 挑一个最能说明"做了什么"的参数
  const detail =
    (typeof args.file_path === 'string' && args.file_path) ||
    (typeof args.path === 'string' && args.path) ||
    (typeof args.pattern === 'string' && args.pattern) ||
    (typeof args.command === 'string' && args.command) ||
    (typeof args.code === 'string' && args.code.split('\n')[0]) ||
    ''
  return detail ? `${label} ${detail}` : label
}

export default function ToolCallBlock({ call }: { call: ToolCallView }) {
  const [open, setOpen] = useState(false)

  const stateMark =
    call.state === 'running' ? (
      <span className="flex items-center gap-1.5 text-ink-faint">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
        进行中
      </span>
    ) : call.state === 'failed' ? (
      <span className="text-danger">失败</span>
    ) : (
      <CheckIcon className="h-3.5 w-3.5 text-success" />
    )

  // 改动量：改过文件的工具才有（+12 −3）
  const hasDiff = typeof call.added === 'number' || typeof call.removed === 'number'

  return (
    <div
      className={[
        'overflow-hidden rounded-xl border transition-colors',
        open ? 'border-line bg-sunken' : 'border-transparent hover:bg-sunken/60',
      ].join(' ')}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs text-ink-muted transition-colors"
      >
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-base text-ink-faint">
          {iconFor(call.name)}
        </span>
        <span className="min-w-0 flex-1 truncate">{summarize(call)}</span>
        {hasDiff ? (
          <span className="shrink-0 font-mono">
            {call.added ? <span className="text-success">+{call.added}</span> : null}
            {call.removed ? <span className="ml-1 text-danger">−{call.removed}</span> : null}
          </span>
        ) : null}
        {call.duration_ms ? (
          <span className="shrink-0 text-ink-faint">{(call.duration_ms / 1000).toFixed(1)}s</span>
        ) : null}
        <span className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-faint">
          {stateMark}
        </span>
      </button>

      {open && (
        <div className="border-t border-line px-2.5 pb-2.5 pt-2 text-xs">
          <div className="text-ink-faint">参数</div>
          <pre className="mt-1 max-h-40 overflow-auto rounded-lg bg-base p-2 text-[11px] leading-relaxed text-ink-muted">
            {JSON.stringify(call.args, null, 2)}
          </pre>
          {call.summary ? (
            <>
              <div className="mt-2 text-ink-faint">结果</div>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-base p-2 text-[11px] leading-relaxed text-ink-muted">
                {call.summary}
              </pre>
            </>
          ) : null}
          {call.error_code ? (
            <div className="mt-2 text-danger">错误码：{call.error_code}</div>
          ) : null}
        </div>
      )}
    </div>
  )
}
