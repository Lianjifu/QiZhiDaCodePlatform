/**
 * 工具调用折叠块。
 *
 * 长得像 Claude 那样：**一行摘要 + 展开看细节**。折叠时只显示
 * 「Ran xxx 1.2s ✓」，展开才给参数与结果摘要。
 *
 * 结果摘要后端已经截断了 —— 工具输出可能有几万 token，原样塞进来会撑爆页面。
 */
import { useState } from 'react'

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
      <span className="text-ink-faint">进行中…</span>
    ) : call.state === 'failed' ? (
      <span className="text-danger">失败</span>
    ) : (
      <span className="text-success">✓</span>
    )

  return (
    <div className="my-1 overflow-hidden rounded-lg border border-line bg-sunken">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-ink-muted transition-colors hover:bg-hover"
      >
        <span className="w-3 shrink-0 text-ink-faint">{open ? '▾' : '▸'}</span>
        <span className="min-w-0 flex-1 truncate">{summarize(call)}</span>
        {call.duration_ms ? (
          <span className="shrink-0 text-ink-faint">{(call.duration_ms / 1000).toFixed(1)}s</span>
        ) : null}
        <span className="shrink-0">{stateMark}</span>
      </button>

      {open && (
        <div className="border-t border-line px-3 py-2 text-xs">
          <div className="text-ink-faint">参数</div>
          <pre className="mt-1 max-h-40 overflow-auto rounded bg-base p-2 text-[11px] leading-relaxed text-ink-muted">
            {JSON.stringify(call.args, null, 2)}
          </pre>
          {call.summary ? (
            <>
              <div className="mt-2 text-ink-faint">结果</div>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-base p-2 text-[11px] leading-relaxed text-ink-muted">
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
