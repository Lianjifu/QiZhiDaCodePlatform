/**
 * 工作目录条：当前会话在哪个目录里干活，以及换目录 / 建 worktree 的入口。
 *
 * 放在聊天页**对话框下方**，而且**只在初始状态**出现 —— 开新会话时选好目录，
 * 聊起来之后它就不占地方了（要换目录去「工作目录」页）。
 *
 * 左边那截点开是环境清单：
 *
 * | 选什么 | 会发生什么 |
 * | --- | --- |
 * | 仓库 | **自动派生一个 worktree**（分支自动生成）并绑上 —— 不用先填分支 |
 * | 本地目录 | 直接绑上，agent 就在那个目录里干活 |
 * | 已有的 worktree | 直接绑上 |
 *
 * 「新建工作区」= 起个名字 + 选一个本地目录。
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import {
  bindEnvironment,
  createWorktree,
  getEnvironmentBinding,
  listEnvironments,
  unbindEnvironment,
} from '../../api/resources'
import { BranchIcon, FolderIcon } from '../../components/icons'
import Button from '../../components/ui/Button'
import { Select, TextInput } from '../../components/ui/Fields'
import Modal from '../../components/ui/Modal'
import type { Environment, EnvironmentList } from '../../types/api'
import { bindableEnvironments, kindLabel } from './format'

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
   * 选中一个仓库 —— **自动派生一个 worktree** 并绑上。
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
  const repos = environments.filter((item) => item.is_repo && item.kind !== 'worktree')
  const worktrees = environments.filter((item) => item.kind === 'worktree')
  const folders = environments.filter((item) => item.kind !== 'worktree' && !item.is_repo)

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

      <Button
        size="sm"
        variant="ghost"
        onClick={() => setCreating(true)}
        disabled={!enabled || (repos.length === 0 && folders.length === 0)}
        title={repos.length === 0 ? '白名单下还没有 git 仓库' : '从仓库派生一个独立检出'}
        className="flex items-center gap-1"
      >
        <BranchIcon className="h-3.5 w-3.5" />
        Worktree
      </Button>

      {error ? (
        <span className="truncate text-[11px] text-danger" title={error}>
          {error}
        </span>
      ) : note ? (
        <span className="truncate text-[11px] text-success">{note}</span>
      ) : null}

      {picking ? (
        <Modal title="选择工作目录" onClose={() => setPicking(false)}>
          <p className="mb-3 text-xs text-ink-muted">
            选一个目录，agent 的文件读写与命令执行就在那里。只能选白名单内的目录。
          </p>

          <Section title="仓库">
            <p className="mb-1.5 text-[11px] text-ink-faint">
              选中就自动开一个新分支 + 一份独立检出，不用另外配置。
            </p>
            {repos.length === 0 ? (
              <Empty text="白名单下还没有 git 仓库" />
            ) : (
              <ul className="space-y-1">
                {repos.map((item) => (
                  <PickRow
                    key={item.path}
                    item={item}
                    hint={item.branch ? `当前 ${item.branch}` : ''}
                    disabled={busy}
                    onClick={() => void chooseRepo(item)}
                  />
                ))}
              </ul>
            )}
          </Section>

          <Section title="本地目录">
            {folders.length === 0 ? (
              <Empty text="白名单下没有可选的目录" />
            ) : (
              <ul className="space-y-1">
                {folders.map((item) => (
                  <PickRow key={item.path} item={item} onClick={() => void bind(item.path)} />
                ))}
              </ul>
            )}
          </Section>

          {worktrees.length > 0 ? (
            <Section title="Worktree">
              <ul className="space-y-1">
                {worktrees.map((item) => (
                  <PickRow
                    key={item.path}
                    item={item}
                    hint={item.branch}
                    onClick={() => void bind(item.path)}
                  />
                ))}
              </ul>
            </Section>
          ) : null}

          <div className="mt-4 flex items-center justify-between gap-2 border-t border-line pt-3">
            <Button size="sm" onClick={() => setCreating(true)}>
              新建工作区
            </Button>
            {binding ? (
              <Button size="sm" variant="ghost" onClick={() => void unbind()}>
                解绑，回到会话自带的工作区
              </Button>
            ) : null}
          </div>
        </Modal>
      ) : null}

      {creating ? (
        <WorktreeDialog
          repos={repos}
          folders={folders}
          onClose={() => setCreating(false)}
          onCreated={async (path, label) => {
            setCreating(false)
            await bind(path, label)
          }}
          onWorktree={async (path) => {
            setCreating(false)
            await bind(path)
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

/** 一组可选目录。 */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-3 last:mb-0">
      <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
        {title}
      </h3>
      {children}
    </section>
  )
}

function Empty({ text }: { text: string }) {
  return <div className="py-2 text-xs text-ink-faint">{text}</div>
}

/** 清单里的一行。 */
function PickRow({
  item,
  hint,
  disabled,
  onClick,
}: {
  item: Environment
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
        className="flex w-full items-center gap-2 rounded-lg border border-line bg-base px-3 py-2 text-left transition-colors hover:bg-hover disabled:opacity-60"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-ink">{item.name || item.path}</span>
          <span className="block truncate font-mono text-[11px] text-ink-faint">{item.path}</span>
        </span>
        <span className="shrink-0 text-[11px] text-ink-muted">
          {kindLabel(item.kind, item.is_repo)}
        </span>
        {hint ? (
          <span className="shrink-0 font-mono text-[11px] text-ink-faint">{hint}</span>
        ) : null}
      </button>
    </li>
  )
}

/**
 * 新建工作区 / 新建 worktree。
 *
 * 两个动作在一处：
 *
 * - **本地目录**：起个名字 + 选一个目录 —— 就是"创建会话工作区"；
 * - **Worktree**：从仓库派生一份独立检出，分支名留空就自动生成。
 */
export function WorktreeDialog({
  repos,
  folders,
  onClose,
  onCreated,
  onWorktree,
}: {
  repos: Environment[]
  folders: Environment[]
  onClose: () => void
  onCreated: (path: string, label: string) => void | Promise<void>
  onWorktree: (path: string) => void | Promise<void>
}) {
  const [mode, setMode] = useState<'folder' | 'worktree'>(
    folders.length > 0 ? 'folder' : 'worktree',
  )
  const [name, setName] = useState('')
  const [folderPath, setFolderPath] = useState(folders[0]?.path ?? '')
  const [repoPath, setRepoPath] = useState(repos[0]?.path ?? '')
  const [branch, setBranch] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    setSaving(true)
    setError('')
    try {
      if (mode === 'folder') {
        if (!folderPath.trim()) {
          setError('选一个目录')
          return
        }
        await onCreated(folderPath.trim(), name.trim())
        return
      }
      const env = await createWorktree({
        repo_path: repoPath,
        branch: branch.trim() || undefined,
      })
      await onWorktree(env.path)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={mode === 'folder' ? '新建工作区' : '新建 worktree'}
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
        {folders.length > 0 && repos.length > 0 ? (
          <div className="flex gap-1 border-b border-line">
            {(
              [
                { key: 'folder', label: '本地目录' },
                { key: 'worktree', label: 'Worktree' },
              ] as const
            ).map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setMode(tab.key)}
                className={[
                  '-mb-px border-b-2 px-3 py-1.5 text-sm transition-colors',
                  mode === tab.key
                    ? 'border-accent text-ink'
                    : 'border-transparent text-ink-muted hover:text-ink',
                ].join(' ')}
              >
                {tab.label}
              </button>
            ))}
          </div>
        ) : null}

        {mode === 'folder' ? (
          <>
            <TextInput
              label="名称"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="我的应用"
              hint="给这个工作区起个名字，之后好认"
            />
            <Select
              label="目录"
              value={folderPath}
              onChange={(event) => setFolderPath(event.target.value)}
              options={folders.map((item) => ({
                value: item.path,
                label: item.name || item.path,
              }))}
              hint="agent 的文件读写与命令执行都在这个目录里"
            />
          </>
        ) : (
          <>
            <Select
              label="仓库"
              value={repoPath}
              onChange={(event) => setRepoPath(event.target.value)}
              options={repos.map((repo) => ({
                value: repo.path,
                label: `${repo.name || repo.path}${repo.branch ? `（${repo.branch}）` : ''}`,
              }))}
            />
            <TextInput
              label="分支名"
              value={branch}
              onChange={(event) => setBranch(event.target.value)}
              placeholder="留空就自动生成"
              hint="worktree 落在平台配置的 WORKTREE_ROOT 下面，按仓库名分组"
            />
          </>
        )}

        {error ? (
          <div className="rounded-lg border border-danger bg-danger-soft px-3 py-2 text-xs text-danger">
            {error}
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
