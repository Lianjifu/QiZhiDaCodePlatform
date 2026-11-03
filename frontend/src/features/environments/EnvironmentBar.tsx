/**
 * 工作目录条：当前会话在哪个目录里干活，以及派生 worktree 的入口。
 *
 * 放在聊天页输入框的上面一行 —— 工作目录跟这一轮做的事直接相关，就该待在
 * 输入框旁边，而不是藏进设置里。
 *
 * 两个动作：
 *
 * - **左边那截**：点开是环境清单，换一个目录 / 解绑；
 * - **右边「Worktree」**：从当前仓库派生一个独立检出（新的分支 + 新的目录），
 *   建完直接绑上 —— 这就是"把活儿切一条分支出去做"的那一步。
 */
import { useCallback, useEffect, useState } from 'react'

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
import { bindableEnvironments } from './format'
import Modal from '../../components/ui/Modal'
import type { Environment, EnvironmentList } from '../../types/api'

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
        setBinding(
          current.path
            ? {
                path: current.path,
                name: current.name,
                kind: current.kind,
                is_repo: current.kind !== 'folder',
                branch: current.branch,
                repo: current.repo,
                dirty: false,
              }
            : null,
        )
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
      const current = await loadBinding()
      setBinding(
        current.path
          ? {
              path: current.path,
              name: current.name,
              kind: current.kind,
              is_repo: current.kind !== 'folder',
              branch: current.branch,
              repo: current.repo,
              dirty: false,
            }
          : null,
      )
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [loadBinding])

  const choose = async (path: string) => {
    try {
      await bindEnvironment(threadId, path)
      setPicking(false)
      setNote('已切换工作目录')
      await refreshBinding()
      onChanged?.()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
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
  const environments = bindableEnvironments(list?.environments ?? [])
  const repos = environments.filter((item) => item.is_repo && item.kind !== 'worktree')

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
        <span className="truncate">
          {binding ? binding.name || binding.path : '会话工作区'}
        </span>
        {binding?.branch ? (
          <span className="shrink-0 font-mono text-[10px] text-ink-faint">{binding.branch}</span>
        ) : null}
      </button>

      <Button
        size="sm"
        variant="ghost"
        onClick={() => setCreating(true)}
        disabled={!enabled || repos.length === 0}
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
            绑上之后，agent 的文件读写与命令执行都在这个目录里。只能选白名单内的目录。
          </p>
          <div className="space-y-1.5">
            {environments.map((item) => (
              <button
                key={item.path}
                type="button"
                onClick={() => void choose(item.path)}
                className={[
                  'flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left transition-colors hover:bg-hover',
                  binding?.path === item.path
                    ? 'border-accent/40 bg-accent-soft/50'
                    : 'border-line bg-base',
                ].join(' ')}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-ink">{item.name || item.path}</span>
                  <span className="block truncate font-mono text-[11px] text-ink-faint">
                    {item.path}
                  </span>
                </span>
                <span className="shrink-0 text-[11px] text-ink-muted">
                  {item.kind === 'worktree' ? 'worktree' : item.is_repo ? '仓库' : '目录'}
                </span>
                {item.branch ? (
                  <span className="shrink-0 font-mono text-[11px] text-ink-faint">{item.branch}</span>
                ) : null}
              </button>
            ))}
          </div>
          {binding ? (
            <div className="mt-3 border-t border-line pt-3">
              <Button size="sm" onClick={() => void unbind()}>
                解绑，回到会话自带的工作区
              </Button>
            </div>
          ) : null}
        </Modal>
      ) : null}

      {creating ? (
        <WorktreeDialog
          repos={repos}
          onClose={() => setCreating(false)}
          onCreated={async (path) => {
            setCreating(false)
            await choose(path)
          }}
        />
      ) : null}
    </div>
  )
}

/** 建 worktree 的弹窗：选仓库、起分支名。 */
export function WorktreeDialog({
  repos,
  onClose,
  onCreated,
}: {
  repos: Environment[]
  onClose: () => void
  onCreated: (path: string) => void | Promise<void>
}) {
  const [repoPath, setRepoPath] = useState(repos[0]?.path ?? '')
  const [branch, setBranch] = useState('')
  const [base, setBase] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!branch.trim()) {
      setError('分支名不能为空')
      return
    }
    setSaving(true)
    setError('')
    try {
      const env = await createWorktree({
        repo_path: repoPath,
        branch: branch.trim(),
        base: base.trim() || null,
      })
      await onCreated(env.path)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="新建 worktree"
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
        <Select
          label="仓库"
          hint="worktree 落在平台配置的 WORKTREE_ROOT 下面，按仓库名分组"
          value={repoPath}
          onChange={(event) => setRepoPath(event.target.value)}
          options={repos.map((repo) => ({
            value: repo.path,
            label: `${repo.name || repo.path}${repo.branch ? `（${repo.branch}）` : ''}`,
          }))}
        />
        <TextInput
          label="分支名"
          required
          value={branch}
          onChange={(event) => setBranch(event.target.value)}
          placeholder="feat/登录页"
          hint="新分支 —— 在这条分支上干活，之后由你决定要不要合回去"
        />
        <TextInput
          label="基点"
          value={base}
          onChange={(event) => setBase(event.target.value)}
          placeholder="main"
          hint="从哪个分支或提交切出来；留空就是当前 HEAD"
        />
        {error ? (
          <div className="rounded-lg border border-danger bg-danger-soft px-3 py-2 text-xs text-danger">
            {error}
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
