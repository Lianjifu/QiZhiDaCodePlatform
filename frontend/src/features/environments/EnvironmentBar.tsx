/**
 * 工作目录条：当前会话在哪个目录里干活，以及换目录的入口。
 *
 * 放在聊天页**对话框下方**，而且**只在初始状态**出现 —— 开新会话时选好目录，
 * 聊起来之后它就不占地方了（要换目录去「工作目录」页）。
 *
 * 左边那截点开是环境清单，三段：
 *
 * | 选什么 | 会发生什么 |
 * | --- | --- |
 * | 仓库 | **自动分配一份独立检出**（分支自动生成）并绑上 —— 不用先填分支 |
 * | 本地目录 | 直接绑上，agent 就在那个目录里干活 |
 * | 已有的 worktree | 直接绑上 |
 *
 * 「新建工作区」= 起个名字 + 选一个本地目录。
 *
 * **没有「新建 worktree」那一档** —— 选中仓库就会自动分配，独立检出不是
 * 一个需要单独配置的动作。
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import {
  bindEnvironment,
  createWorktree,
  getEnvironmentBinding,
  listEnvironments,
  unbindEnvironment,
} from '../../api/resources'
import { BranchIcon, FolderIcon, SearchIcon } from '../../components/icons'
import Button from '../../components/ui/Button'
import { TextInput } from '../../components/ui/Fields'
import Modal from '../../components/ui/Modal'
import type { Environment, EnvironmentList } from '../../types/api'
import FolderBrowser from './FolderBrowser'
import { bindableEnvironments, filterEnvironments } from './format'

export default function EnvironmentBar({
  threadId,
  onChanged,
}: {
  threadId: string
  /** 绑定变了之后通知外层（状态面板要刷新） */
  onChanged?: () => void
}) {
  const [list, setList] = useState<EnvironmentList | null>(null)
  const [binding, setBinding] = useState<Environment | null>(null)
  const [picking, setPicking] = useState(false)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  const load = useCallback(async () => listEnvironments(), [])

  useEffect(() => {
    let cancelled = false
    void load()
      .then((data) => {
        if (cancelled) return
        setList(data)
        setError('')
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      })
    return () => {
      cancelled = true
    }
  }, [load])

  // 绑定状态从绑定接口拿 —— 与清单分开，清单里没有"当前是哪个"这层意思
  const loadBinding = useCallback(async () => getEnvironmentBinding(threadId), [threadId])

  useEffect(() => {
    let cancelled = false
    void loadBinding()
      .then((current) => {
        if (cancelled) return
        setBinding(toEnvironment(current))
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      })
    return () => {
      cancelled = true
    }
  }, [loadBinding])

  /** 绑定变了之后刷新那一条（事件处理器里调用，不是 effect）。 */
  const refreshBinding = useCallback(async () => {
    try {
      setBinding(toEnvironment(await loadBinding()))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [loadBinding])

  const bind = async (path: string, label?: string) => {
    try {
      await bindEnvironment(threadId, path, label)
      setPicking(false)
      setNote(label ? `已创建工作区「${label}」` : '已切换工作目录')
      await refreshBinding()
      onChanged?.()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  /**
   * 选中一个仓库 —— **自动分配一份独立检出**并绑上。
   *
   * 不用先填分支名：分支由平台生成（``qzd/<仓库>-<随机>``）。
   */
  const chooseRepo = async (repo: Environment) => {
    setBusy(true)
    setError('')
    try {
      const env = await createWorktree({ repo_path: repo.path })
      await bindEnvironment(threadId, env.path)
      setPicking(false)
      setNote(`已在 ${repo.name} 上开了新分支 ${env.branch}`)
      await refreshBinding()
      onChanged?.()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const unbind = async () => {
    try {
      await unbindEnvironment(threadId)
      setPicking(false)
      setNote('已回到会话自带的工作区')
      await refreshBinding()
      onChanged?.()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const enabled = list?.enabled ?? false
  const environments = useMemo(() => bindableEnvironments(list?.environments ?? []), [list])

  return (
    <div className="flex items-center gap-2 px-1">
      <button
        type="button"
        onClick={() => setPicking(true)}
        disabled={!enabled}
        className="flex min-w-0 items-center gap-1.5 rounded-lg border border-line bg-base px-2 py-1 text-xs text-ink-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-60"
        title={enabled ? '选择工作目录' : '平台还没配置可用的仓库目录'}
      >
        <FolderIcon className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">{binding ? binding.name || binding.path : '会话工作区'}</span>
        {binding?.branch ? (
          <span className="shrink-0 font-mono text-[10px] text-ink-faint">{binding.branch}</span>
        ) : null}
      </button>

      {error ? (
        <span className="truncate text-[11px] text-danger" title={error}>
          {error}
        </span>
      ) : note ? (
        <span className="truncate text-[11px] text-success">{note}</span>
      ) : null}

      {picking ? (
        <EnvironmentPicker
          environments={environments}
          busy={busy}
          onClose={() => setPicking(false)}
          onPickFolder={(item) => void bind(item.path)}
          onPickRepo={(item) => void chooseRepo(item)}
          onCreate={() => {
            // **先把选择器关掉** —— 两个弹窗叠在一起会串
            setPicking(false)
            setCreating(true)
          }}
          onUnbind={binding ? () => void unbind() : undefined}
        />
      ) : null}

      {creating ? (
        <CreateWorkspaceDialog
          onClose={() => setCreating(false)}
          onCreated={async (path, label) => {
            setCreating(false)
            await bind(path, label)
          }}
        />
      ) : null}
    </div>
  )
}

/** 绑定记录 → 环境（给清单里用）。 */
function toEnvironment(current: {
  path: string
  name: string
  kind: string
  branch: string
  repo: string | null
}): Environment | null {
  if (!current.path) return null
  return {
    path: current.path,
    name: current.name,
    kind: current.kind,
    is_repo: current.kind !== 'folder',
    branch: current.branch,
    repo: current.repo,
    dirty: false,
  }
}

/**
 * 选择工作目录。
 *
 * 三段：**仓库**（选中就自动分配独立检出）/ **本地目录** / **已有的
 * worktree**。白名单下的目录可能有几十个，所以带一个搜索框。
 */
function EnvironmentPicker({
  environments,
  busy,
  onClose,
  onPickFolder,
  onPickRepo,
  onCreate,
  onUnbind,
}: {
  environments: Environment[]
  busy: boolean
  onClose: () => void
  onPickFolder: (item: Environment) => void
  onPickRepo: (item: Environment) => void
  onCreate: () => void
  onUnbind?: () => void
}) {
  const [query, setQuery] = useState('')

  const matched = useMemo(() => filterEnvironments(environments, query), [environments, query])
  const repos = matched.filter((item) => item.is_repo && item.kind !== 'worktree')
  const worktrees = matched.filter((item) => item.kind === 'worktree')
  const folders = matched.filter((item) => item.kind !== 'worktree' && !item.is_repo)

  return (
    <Modal title="选择工作目录" onClose={onClose} wide>
      <div className="space-y-4">
        <div className="relative">
          <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-faint" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜名字或路径…"
            className="w-full rounded-lg border border-line bg-base py-1.5 pl-8 pr-2.5 text-sm text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent focus:shadow-[0_0_0_3px_var(--ring)]"
          />
        </div>

        <Group title="仓库" hint="选中就自动开一个新分支 + 一份独立检出">
          {repos.length === 0 ? (
            <Empty text="没有匹配的仓库" />
          ) : (
            <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line">
              {repos.map((item) => (
                <Row
                  key={item.path}
                  item={item}
                  icon={<BranchIcon className="h-3.5 w-3.5" />}
                  badge="仓库"
                  hint={item.branch ? `当前 ${item.branch}` : ''}
                  disabled={busy}
                  onClick={() => onPickRepo(item)}
                />
              ))}
            </ul>
          )}
        </Group>

        <Group title="本地目录" hint="agent 就在这个目录里读写文件、跑命令">
          {folders.length === 0 ? (
            <Empty text="没有匹配的目录" />
          ) : (
            <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line">
              {folders.map((item) => (
                <Row
                  key={item.path}
                  item={item}
                  icon={<FolderIcon className="h-3.5 w-3.5" />}
                  badge="目录"
                  onClick={() => onPickFolder(item)}
                />
              ))}
            </ul>
          )}
        </Group>

        {worktrees.length > 0 ? (
          <Group title="Worktree" hint="已经开好的独立检出">
            <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line">
              {worktrees.map((item) => (
                <Row
                  key={item.path}
                  item={item}
                  icon={<BranchIcon className="h-3.5 w-3.5" />}
                  badge="Worktree"
                  hint={item.branch}
                  onClick={() => onPickFolder(item)}
                />
              ))}
            </ul>
          </Group>
        ) : null}

        <div className="flex items-center justify-between gap-2 border-t border-line pt-3">
          <Button size="sm" onClick={onCreate}>
            新建工作区
          </Button>
          {onUnbind ? (
            <Button size="sm" variant="ghost" onClick={onUnbind}>
              解绑，回到会话自带的工作区
            </Button>
          ) : null}
        </div>
      </div>
    </Modal>
  )
}

/** 一组可选目录。 */
function Group({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: ReactNode
}) {
  return (
    <section>
      <div className="mb-1.5 flex items-baseline gap-2">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{title}</h3>
        {hint ? <span className="truncate text-[11px] text-ink-faint">{hint}</span> : null}
      </div>
      {children}
    </section>
  )
}

function Empty({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-xs text-ink-faint">
      {text}
    </div>
  )
}

/** 清单里的一行。 */
function Row({
  item,
  icon,
  badge,
  hint,
  disabled,
  onClick,
}: {
  item: Environment
  icon: ReactNode
  badge: string
  hint?: string
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className="flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-hover disabled:opacity-60"
      >
        <span className="shrink-0 text-ink-faint">{icon}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-ink">{item.name || item.path}</span>
          <span className="block truncate font-mono text-[11px] text-ink-faint">{item.path}</span>
        </span>
        {hint ? (
          <span className="shrink-0 font-mono text-[11px] text-ink-faint">{hint}</span>
        ) : null}
        <span className="shrink-0 rounded bg-sunken px-1.5 py-0.5 text-[10px] text-ink-muted">
          {badge}
        </span>
      </button>
    </li>
  )
}

/**
 * 新建工作区：起个名字 + 选一个本地目录。
 *
 * **只有这一种** —— 独立检出不是"新建"出来的，选中仓库就会自动分配。
 */
export function CreateWorkspaceDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (path: string, label: string) => void | Promise<void>
}) {
  const [name, setName] = useState('')
  const [folderPath, setFolderPath] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!folderPath.trim()) {
      setError('选一个目录')
      return
    }
    setSaving(true)
    setError('')
    try {
      await onCreated(folderPath.trim(), name.trim())
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="新建工作区"
      onClose={onClose}
      footer={
        <>
          <Button size="sm" onClick={onClose}>
            取消
          </Button>
          <Button size="sm" variant="primary" onClick={() => void submit()} disabled={saving}>
            {saving ? '创建中…' : '创建并切换'}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextInput
          label="名称"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="我的应用"
          hint="给这个工作区起个名字，之后好认"
        />
        <div>
          <span className="mb-1 block text-xs font-medium text-ink-muted">
            目录
            <span className="ml-0.5 text-danger">*</span>
          </span>
          <FolderBrowser value={folderPath} onChange={setFolderPath} />
          <span className="mt-1 block text-[11px] text-ink-faint">
            agent 的文件读写与命令执行都在这个目录里 —— 只能选白名单内的目录
          </span>
        </div>
        {error ? (
          <div className="rounded-lg border border-danger bg-danger-soft px-3 py-2 text-xs text-danger">
            {error}
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
