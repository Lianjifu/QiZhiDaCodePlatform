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
 * - **新建 worktree**：选一个仓库就自动开一份独立检出（分支自动生成）。
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { createWorktree, deleteWorktree, listEnvironments } from '../api/resources'
import Badge from '../components/ui/Badge'
import Banner from '../components/ui/Banner'
import Button from '../components/ui/Button'
import EmptyState from '../components/ui/EmptyState'
import { Select } from '../components/ui/Fields'
import Modal from '../components/ui/Modal'
import PageHeader from '../components/ui/PageHeader'
import { bindableEnvironments } from '../features/environments/format'
import { useConfirm } from '../components/ui/useConfirm'
import type { Environment, EnvironmentList } from '../types/api'

export default function EnvironmentsPage() {
  const { confirm, dialog: confirmDialog } = useConfirm()
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
    <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6 sm:py-6">
      <PageHeader
        title="工作目录"
        description="把真实的代码仓库绑成会话的工作目录。绑上之后 agent 就在那个目录里读写文件、跑命令。"
        actions={
          <Button
            variant="primary"
            size="lg"
            onClick={() => setCreating(true)}
            disabled={!list?.enabled || repos.length === 0}
          >
            新建 worktree
          </Button>
        }
      />

      {error ? (
        <Banner tone="danger" className="mt-4">
          {error}
        </Banner>
      ) : null}
      {note ? (
        <Banner tone="success" className="mt-4">
          {note}
        </Banner>
      ) : null}

      {list && !list.enabled ? (
        <div className="mt-5 rounded-xl border border-line bg-raised p-4 shadow-1">
          <p className="text-sm font-medium text-ink">平台还没有配置可用的仓库目录。</p>
          <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
            在后端的 <code className="font-mono">.env</code> 里配置{' '}
            <code className="font-mono">REPO_ROOTS</code>（逗号分隔的绝对路径），
            只有这些根目录下的仓库才能作为工作目录。
          </p>
          <p className="mt-2 text-xs leading-relaxed text-ink-faint">
            平台目前没有鉴权，这道白名单是唯一的闸门 —— 所以默认是「一个都不许」。
          </p>
        </div>
      ) : null}

      {list?.enabled ? (
        <div className="mt-5 space-y-5">
          <Section title="允许的根目录" count={list.roots.length}>
            <ul className="divide-y divide-line">
              {list.roots.map((root) => (
                <li key={root} className="px-4 py-2.5 font-mono text-xs text-ink-muted">
                  {root}
                </li>
              ))}
            </ul>
          </Section>

          <Section title="仓库" count={repos.length}>
            {repos.length === 0 ? (
              <Empty title="白名单下还没有 git 仓库" />
            ) : (
              <ul className="divide-y divide-line">
                {repos.map((item) => (
                  <Row key={item.path} item={item} />
                ))}
              </ul>
            )}
          </Section>

          <Section title="Worktree" count={worktrees.length}>
            {worktrees.length === 0 ? (
              <Empty
                title="还没有 worktree"
                description="从一个仓库派生一份独立检出 —— 各自一个分支，互不干扰。"
              />
            ) : (
              <ul className="divide-y divide-line">
                {worktrees.map((item) => (
                  <Row
                    key={item.path}
                    item={item}
                    onDelete={() => {
                      void (async () => {
                        const ok = await confirm({
                          title: `删掉 worktree「${item.name}」？`,
                          description:
                            '这个目录连着里面的改动一起没了。分支还留在仓库里，随时能再建回来。',
                          confirmText: '删掉',
                          danger: true,
                        })
                        if (!ok) return
                        await run(() => deleteWorktree(item.path), '已删除 worktree')
                      })()
                    }}
                  />
                ))}
              </ul>
            )}
          </Section>

          <Section title="目录" count={folders.length}>
            {folders.length === 0 ? (
              <Empty title="白名单下没有普通目录" />
            ) : (
              <ul className="divide-y divide-line">
                {folders.map((item) => (
                  <Row key={item.path} item={item} />
                ))}
              </ul>
            )}
          </Section>
        </div>
      ) : null}

      {/*
        扫目录找仓库要两秒多 —— 干等着不像坏了，给个骨架。
        （后端那一步是「每个白名单根目录下逐个看是不是 git 仓库」。）
      */}
      {!list ? <Skeleton /> : null}

      {confirmDialog}

      {creating ? (
        <NewWorktreeDialog
          repos={repos}
          onClose={() => setCreating(false)}
          onCreated={async () => {
            setCreating(false)
            setNote('已创建 worktree')
            await load()
          }}
        />
      ) : null}
    </div>
  )
}

/**
 * 新建 worktree：选一个仓库，分支由平台自动生成。
 *
 * **没有分支名这一栏** —— 选中仓库就该直接能用，独立检出不是一个需要
 * 单独配置的动作（跟聊天页那个选择器一致）。
 */
function NewWorktreeDialog({
  repos,
  onClose,
  onCreated,
}: {
  repos: Environment[]
  onClose: () => void
  onCreated: () => void | Promise<void>
}) {
  const [repoPath, setRepoPath] = useState(repos[0]?.path ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    setSaving(true)
    setError('')
    try {
      await createWorktree({ repo_path: repoPath })
      await onCreated()
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
      description="自动开一个新分支 + 一份独立检出"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button variant="primary" loading={saving} onClick={() => void submit()}>
            创建
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Select
          label="仓库"
          value={repoPath}
          onChange={(event) => setRepoPath(event.target.value)}
          options={repos.map((repo) => ({
            value: repo.path,
            label: `${repo.name || repo.path}${repo.branch ? `（${repo.branch}）` : ''}`,
          }))}
          hint="分支名由平台生成"
        />
        {error ? <Banner tone="danger">{error}</Banner> : null}
      </div>
    </Modal>
  )
}

function Section({
  title,
  count,
  children,
}: {
  title: string
  count?: number
  children: ReactNode
}) {
  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-xs font-medium tracking-wide text-ink-muted">{title}</h2>
        {count !== undefined ? (
          <span className="rounded-md bg-sunken px-1.5 py-px text-[10px] font-medium tabular-nums text-ink-faint">
            {count}
          </span>
        ) : null}
      </div>
      <div className="overflow-hidden rounded-xl border border-line bg-raised shadow-1">
        {children}
      </div>
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
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3">
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm text-ink">{item.name || item.path}</span>
          {/* worktree 也是仓库 —— 只标一个，不然两个标签说同一件事 */}
          {item.kind === 'worktree' ? <Badge tone="accent">Worktree</Badge> : null}
          {item.is_repo && item.kind !== 'worktree' ? <Badge tone="accent">仓库</Badge> : null}
        </span>
        <span className="mt-0.5 block truncate font-mono text-[11px] text-ink-faint">{item.path}</span>
      </span>
      {item.branch ? (
        <span className="shrink-0 font-mono text-[11px] text-ink-muted">{item.branch}</span>
      ) : null}
      {item.dirty ? (
        <span className="shrink-0 text-[11px] text-warning">有未提交的改动</span>
      ) : null}
      {onDelete ? (
        <Button size="sm" variant="ghost" onClick={onDelete} className="ml-auto shrink-0">
          <span className="text-danger">删除</span>
        </Button>
      ) : null}
    </li>
  )
}

function Empty({ title, description }: { title: string; description?: string }) {
  return <EmptyState title={title} description={description} />
}

/** 加载中的骨架 —— 扫目录要两秒多，白屏看着像坏了。 */
function Skeleton() {
  return (
    <div className="mt-5 space-y-5" aria-hidden="true">
      {[0, 1, 2].map((group) => (
        <div key={group}>
          <div className="mb-2 h-3 w-24 rounded bg-sunken" />
          <div className="overflow-hidden rounded-xl border border-line bg-raised">
            {[0, 1, 2].map((row) => (
              <div key={row} className="flex items-center gap-3 border-b border-line px-4 py-3">
                <div className="h-3 w-40 rounded bg-sunken" />
                <div className="h-3 flex-1 rounded bg-sunken/60" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
