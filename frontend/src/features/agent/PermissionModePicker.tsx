/**
 * 权限模式选择器（输入框底部那一栏）。
 *
 * 语义对齐 Claude Code 的权限模式 —— 这一轮/这个会话「要不要人批」由它决定：
 *
 * | 模式 | 行为 |
 * | --- | --- |
 * | 自动 | 按风险档位：低档自动放行，中档与高档要人批 |
 * | 手动 | 任何改动都要人批（只读不问） |
 * | 接受编辑 | 文件编辑自动放行，执行命令 / 联网 / 破坏性照旧问 |
 * | 只读 | 写文件与执行命令的工具**不暴露给模型**，只能读与规划 |
 * | 绕过权限 | 全放行（含破坏性） |
 *
 * 模式是**会话级**的，存在后端（别的进程也要看得见 —— 审批恢复可能落在
 * 另一个进程上）。改完立刻生效：下一轮就用新模式。
 */
import { useEffect, useRef, useState } from 'react'

import { getSessionPermission, setSessionPermission } from '../../api/resources'
import { ShieldIcon } from '../../components/icons'
import { PERMISSION_MODES, permissionLabel } from './permissionModes'

export default function PermissionModePicker({
  threadId,
  onChange,
}: {
  threadId: string
  /** 模式变了（用来刷新界面上的其它地方） */
  onChange?: (mode: string) => void
}) {
  const [mode, setMode] = useState('auto')
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)

  // 当前模式：后端说了算（默认值来自全局配置）
  useEffect(() => {
    if (!threadId) return
    let cancelled = false
    void getSessionPermission(threadId)
      .then((result) => {
        if (cancelled) return
        setMode(result.permission_mode)
        setOpen(false)
      })
      .catch(() => {
        // 读不到就用 auto —— 显示错了不影响干活
      })
    return () => {
      cancelled = true
    }
  }, [threadId])

  // 点外面收起菜单
  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const choose = async (key: string) => {
    setOpen(false)
    if (key === mode) return
    if (key === 'bypass') {
      // 绕过权限 = 连破坏性操作都不问 —— 这一步要点确认
      const ok = window.confirm(
        '绕过权限模式：所有操作都会直接执行，包括删除文件、破坏性命令。确定要切到这个模式吗？',
      )
      if (!ok) return
    }
    setBusy(true)
    setError('')
    try {
      const result = await setSessionPermission(threadId, key)
      setMode(result.permission_mode)
      onChange?.(result.permission_mode)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={busy}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-ink-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-50"
        title="权限模式：这一轮要不要人批"
      >
        <ShieldIcon className="h-3.5 w-3.5 text-ink-faint" />
        {permissionLabel(mode)}
        <span className="text-ink-faint">▾</span>
      </button>

      {open ? (
        <div className="absolute bottom-full left-0 mb-1 w-72 overflow-hidden rounded-xl border border-line bg-overlay shadow-lg">
          <div className="border-b border-line px-3 py-2 text-[11px] text-ink-faint">
            权限模式 —— 这一轮要不要人批
          </div>
          {PERMISSION_MODES.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => void choose(item.key)}
              className={[
                'flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left transition-colors hover:bg-hover',
                item.key === mode ? 'bg-sunken' : '',
              ].join(' ')}
            >
              <span className="flex items-center gap-1.5 text-xs font-medium text-ink">
                {item.label}
                {item.recommended ? (
                  <span className="rounded bg-sunken px-1 py-0.5 text-[10px] text-ink-faint">
                    推荐
                  </span>
                ) : null}
                {item.key === mode ? <span className="text-accent">✓</span> : null}
              </span>
              <span className="text-[11px] leading-relaxed text-ink-faint">{item.hint}</span>
            </button>
          ))}
          {error ? <div className="px-3 py-2 text-[11px] text-danger">{error}</div> : null}
        </div>
      ) : null}
    </div>
  )
}
