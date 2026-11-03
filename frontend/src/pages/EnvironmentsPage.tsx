/**
 * 工作目录（环境）。
 *
 * 把真实的代码仓库绑成会话的工作目录 —— 绑上之后 agent 的文件读写、命令执行
 * 都在那个目录里，而不是平台自带的空白工作区。
 *
 * 页面上的东西：
 *
 * - **白名单根目录**：平台允许作为工作目录的地方（没配就是全禁）；
 * - **环境清单**：白名单下的目录、仓库，以及平台派生出来的 worktree；
 * - **新建 worktree**：从仓库切一条新分支 + 一个新目录出来。
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { deleteWorktree, listEnvironments } from '../api/resources'
import Button from '../components/ui/Button'
import { WorktreeDialog } from '../features/environments/EnvironmentBar'
import { bindableEnvironments } from '../features/environments/format'
import type { Environment, EnvironmentList } from '../types/api'

export default function EnvironmentsPage() {
  const [list, setList] = useState<EnvironmentList | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [creating, setCreating] = useState(false)

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

  const run = async (action: () => Promise<unknown>, message: string) => {
    try {
      await action()
      setNote(message)
      setList(await load())
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const environments = bindableEnvironments(list?.environments ?? [])
  const repos = environments.filter((item) => item.is_repo && item.kind !== 'worktree')
  const worktrees = environments.filter((item) => item.kind === 'worktree')
  const folders = environments.filter((item) => item.kind !== 'worktree' && !item.is_repo)

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">工作目录</h1>
          <p className="mt-1 text-sm text-ink-muted">
            把真实的代码仓库绑成会话的工作目录。绑上之后 agent 就在那个目录里读写文件、跑命令。
          </p>
        </div>
        <Button
          variant="primary"
          onClick={() => setCreating(true)}
          disabled={!list?.enabled || repos.length === 0}
        >
          新建 worktree
        </Button>
      </div>

      {error ? (
        <div className="mt-4 rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </div>
      ) : null}
      {note ? (
        <div className="mt-4 rounded-lg border border-success bg-success-soft px-3 py-2 text-sm text-success">
          {note}
        </div>
      ) : null}

      {list && !list.enabled ? (
        <div className="mt-4 rounded-lg border border-line bg-raised px-4 py-3 text-sm text-ink-muted">
          <p className="text-ink">平台还没有配置可用的仓库目录。</p>
          <p className="mt-1 text-xs">
            在后端的 <code className="font-mono">.env</code> 里配置{' '}
            <code className="font-mono">REPO_ROOTS</code>（逗号分隔的绝对路径），
            只有这些根目录下的仓库才能作为工作目录。
            <br />
            平台目前没有鉴权，这道白名单是唯一的闸门 —— 所以默认是「一个都不许」。
          </p>
        </div>
      ) : null}

      {list?.enabled ? (
        <>
          <Section title="允许的根目录">
            <ul className="space-y-1">
              {list.roots.map((root) => (
                <li key={root} className="font-mono text-xs text-ink-muted">
                  {root}
                </li>
              ))}
            </ul>
          </Section>

          <Section title="仓库">
            {repos.length === 0 ? (
              <Empty text="白名单下还没有 git 仓库" />
            ) : (
              <ul className="divide-y divide-line">
                {repos.map((item) => (
                  <Row key={item.path} item={item} />
                ))}
              </ul>
            )}
          </Section>

          <Section title="Worktree">
            {worktrees.length === 0 ? (
              <Empty text="还没有 worktree" />
            ) : (
              <ul className="divide-y divide-line">
                {worktrees.map((item) => (
                  <Row
                    key={item.path}
                    item={item}
                    onDelete={() => {
                      if (!window.confirm(`删掉 worktree「${item.name}」？分支还留在仓库里。`)) return
                      void run(() => deleteWorktree(item.path), '已删除 worktree')
                    }}
                  />
                ))}
              </ul>
            )}
          </Section>

          <Section title="目录">
            {folders.length === 0 ? (
              <Empty text="白名单下没有普通目录" />
            ) : (
              <ul className="divide-y divide-line">
                {folders.map((item) => (
                  <Row key={item.path} item={item} />
                ))}
              </ul>
            )}
          </Section>
        </>
      ) : null}

      {creating ? (
        <WorktreeDialog
          repos={repos}
          folders={folders}
          onClose={() => setCreating(false)}
          onCreated={async () => {
            setCreating(false)
            await load()
          }}
          onWorktree={async () => {
            setCreating(false)
            setNote('已创建 worktree')
            await load()
          }}
        />
      ) : null}
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-6">
      <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-faint">{title}</h2>
      <div className="overflow-hidden rounded-lg border border-line bg-raised">{children}</div>
    </section>
  )
}

function Row({
  item,
  onDelete,
}: {
  item: Environment
  onDelete?: () => void
}) {
  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-ink">{item.name || item.path}</span>
        <span className="block truncate font-mono text-[11px] text-ink-faint">{item.path}</span>
      </span>
      {item.branch ? (
        <span className="shrink-0 font-mono text-[11px] text-ink-muted">{item.branch}</span>
      ) : null}
      {item.dirty ? (
        <span className="shrink-0 text-[11px] text-ink-faint">有未提交的改动</span>
      ) : null}
      {onDelete ? (
        <button
          type="button"
          onClick={onDelete}
          className="shrink-0 text-xs text-ink-faint transition-colors hover:text-danger"
        >
          删除
        </button>
      ) : null}
    </li>
  )
}

function Empty({ text }: { text: string }) {
  return <div className="px-4 py-6 text-center text-sm text-ink-faint">{text}</div>
}
