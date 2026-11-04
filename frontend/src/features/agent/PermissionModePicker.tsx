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
import ConfirmDialog from '../../components/ui/ConfirmDialog'
import { PERMISSION_MODES, permissionLabel } from './permissionModes'

/**
 * 权限模式的读取结果。
 *
 * **把 threadId 一起记着** —— 换会话时上一个会话的结果不能顶着显示，
 * 那是另一个会话的模式。
 */
interface LoadedPermission {
  threadId: string
  /** 还没读到 / 读失败时都是 null —— **不能拿具体模式顶上** */
  mode: string | null
  error: string
  /** 平台有没有开 bypass 总闸 —— 没开时那一项不可选 */
  allowBypass: boolean
}

export default function PermissionModePicker({
  threadId,
  onChange,
}: {
  threadId: string
  /** 模式变了（用来刷新界面上的其它地方） */
  onChange?: (mode: string) => void
}) {
  const [loaded, setLoaded] = useState<LoadedPermission>({
    threadId,
    mode: null,
    error: '',
    allowBypass: false,
  })
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  /** 待确认的动作（切到绕过权限要问一句） */
  const [confirming, setConfirming] = useState<string | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  /** 当前会话读到的模式 —— 别的会话的结果不算数。 */
  const current: LoadedPermission =
    loaded.threadId === threadId
      ? loaded
      : { threadId, mode: null, error: '', allowBypass: false }
  const mode = current.mode

  // 当前模式：后端说了算（默认值来自全局配置）
  useEffect(() => {
    if (!threadId) return
    let cancelled = false
    void getSessionPermission(threadId)
      .then((result) => {
        if (cancelled) return
        setLoaded({
          threadId,
          mode: result.permission_mode,
          error: '',
          allowBypass: result.allow_bypass,
        })
        setOpen(false)
      })
      .catch((cause) => {
        if (cancelled) return
        // **读失败就明说读失败** —— 早先这里静默吞掉错误、初始态写死 auto，
        // 真实模式是 bypass 时界面却显示「自动」，用户以为会有人批。
        setLoaded({
          threadId,
          mode: null,
          error: cause instanceof Error ? cause.message : String(cause),
          allowBypass: false,
        })
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
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const choose = async (key: string) => {
    setOpen(false)
    if (key === mode) return
    if (key === 'bypass') {
      // **总闸没开就明说** —— 点了才被后端拒，用户会以为是自己操作错了
      if (!current.allowBypass) {
        setError('平台没有开启「绕过权限」—— 它会跳过所有人工审批，需要管理员在配置里显式打开。')
        return
      }
      // 绕过权限 = 连破坏性操作都不问 —— 这一步要点确认
      setConfirming(key)
      return
    }
    await apply(key)
  }

  const apply = async (key: string) => {
    setBusy(true)
    setError('')
    try {
      const result = await setSessionPermission(threadId, key)
      setLoaded({
        threadId,
        mode: result.permission_mode,
        error: '',
        allowBypass: result.allow_bypass,
      })
      onChange?.(result.permission_mode)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
      setConfirming(null)
    }
  }

  /** 按钮上显示的字 —— **读不到就不显示某个具体模式**。 */
  const label = current.error
    ? '权限模式读取失败'
    : mode === null
      ? '权限模式读取中…'
      : permissionLabel(mode)

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={busy}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex shrink-0 items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs whitespace-nowrap text-ink-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-50"
        title="权限模式：这一轮要不要人批"
      >
        <ShieldIcon className="h-3.5 w-3.5 text-ink-faint" />
        {label}
        <span className="text-ink-faint">▾</span>
      </button>

      {confirming === 'bypass' ? (
        <ConfirmDialog
          title="切到绕过权限？"
          description="绕过权限模式里所有操作都会直接执行 —— 包括删除文件、破坏性命令、联网请求，平台不会再问。这个会话剩下的操作都不会有人工审批。"
          confirmText="切到绕过权限"
          danger
          onConfirm={() => void apply('bypass')}
          onCancel={() => setConfirming(null)}
        />
      ) : null}

      {open ? (
        <div className="absolute bottom-full left-0 mb-1 w-72 overflow-hidden rounded-xl border border-line bg-overlay shadow-lg">
          <div className="border-b border-line px-3 py-2 text-[11px] text-ink-faint">
            权限模式 —— 这一轮要不要人批
          </div>
          {current.error ? (
            <div className="px-3 py-2 text-[11px] text-danger">
              读取失败：{current.error} —— 下面选一个就能改过来。
            </div>
          ) : null}
          {PERMISSION_MODES.map((item) => {
            // **bypass 没开总闸就是不可选** —— 别让用户点了才被拒
            const disabled = item.key === 'bypass' && !current.allowBypass
            return (
            <button
              key={item.key}
              type="button"
              disabled={disabled}
              onClick={() => void choose(item.key)}
              className={[
                'flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left transition-colors hover:bg-hover',
                item.key === mode ? 'bg-sunken' : '',
                disabled ? 'cursor-not-allowed opacity-50 hover:bg-transparent' : '',
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
              <span className="text-[11px] leading-relaxed text-ink-faint">
                {disabled ? '（平台没有开启这一项）' : item.hint}
              </span>
            </button>
            )
          })}
          {error ? <div className="px-3 py-2 text-[11px] text-danger">{error}</div> : null}
        </div>
      ) : null}
    </div>
  )
}
