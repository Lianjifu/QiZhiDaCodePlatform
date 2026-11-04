/**
 * 工作区文件。
 *
 * 上传件与 agent 的产物都在这儿。附件是给对话用的 —— 点「带进对话」把
 * 虚拟路径塞进当前会话。
 *
 * **看的是当前会话的工作区** —— 文件按会话隔离，不是一个全局目录。
 *
 * 能做的：上传（新增）、预览（查看）、改内容与改名（更改）、删除。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'

import {
  archiveUrl,
  deleteFile,
  downloadUrl,
  listFiles,
  readFileText,
  renameFile,
  saveFile,
  uploadFile,
} from '../api/resources'
import Badge from '../components/ui/Badge'
import Banner from '../components/ui/Banner'
import Button from '../components/ui/Button'
import DataTable, { type Column } from '../components/ui/DataTable'
import EmptyState from '../components/ui/EmptyState'
import { TextInput } from '../components/ui/Fields'
import Modal from '../components/ui/Modal'
import PageHeader from '../components/ui/PageHeader'
import { useConfirm } from '../components/ui/useConfirm'
import { humanSize, formatTime } from '../lib/format'
import { useSessions } from '../features/sessions/store'
import type { WorkspaceFile } from '../types/api'

/** 超过这个大小就不在浏览器里看了 —— 文本预览会卡。 */
const MAX_PREVIEW = 512 * 1024

/** 这些扩展名当文本看（有些服务端 MIME 判断不准）。 */
const TEXT_EXTENSIONS = new Set([
  'txt', 'md', 'markdown', 'json', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf',
  'py', 'js', 'jsx', 'ts', 'tsx', 'vue', 'svelte', 'html', 'htm', 'css', 'scss',
  'sh', 'bash', 'zsh', 'sql', 'go', 'rs', 'java', 'kt', 'rb', 'php', 'c', 'h',
  'cpp', 'hpp', 'cs', 'swift', 'lua', 'r', 'dart', 'xml', 'csv', 'log', 'env',
])

/** 能不能当文本预览 / 编辑。 */
function isTextual(file: WorkspaceFile): boolean {
  if (file.mime_type.startsWith('text/')) return true
  if (file.mime_type === 'application/json') return true
  const ext = file.filename.split('.').pop()?.toLowerCase() ?? ''
  return TEXT_EXTENSIONS.has(ext)
}

export default function FilesPage() {
  const { confirm, dialog: confirmDialog } = useConfirm()
  const [files, setFiles] = useState<WorkspaceFile[]>([])
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [uploading, setUploading] = useState(false)
  const [filter, setFilter] = useState('')
  const [searchParams] = useSearchParams()
  const { sessions } = useSessions()

  // 正在预览 / 编辑 / 改名的文件
  const [previewing, setPreviewing] = useState<WorkspaceFile | null>(null)
  const [editing, setEditing] = useState<{ file: WorkspaceFile; content: string } | null>(null)
  const [renaming, setRenaming] = useState<WorkspaceFile | null>(null)
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)

  // 文件按会话隔离 —— 看的是 URL 里指的那个会话。
  // 没指就退到最近用过的那个会话（侧栏进「文件」时会把当前会话带过来）。
  const threadId = useMemo(() => {
    const fromUrl = searchParams.get('thread')
    if (fromUrl) return fromUrl
    const latest = [...sessions].sort((a, b) => b.updatedAt - a.updatedAt)[0]
    return latest?.threadId ?? 'default'
  }, [searchParams, sessions])

  const refresh = useCallback(async () => {
    try {
      const result = await listFiles(threadId)
      setFiles(result.files)
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [threadId])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const result = await listFiles(threadId)
        if (cancelled) return
        setFiles(result.files)
        setError('')
      } catch (cause) {
        if (cancelled) return
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [threadId])

  const onUpload = async (selected: FileList | null) => {
    if (!selected || selected.length === 0) return
    setUploading(true)
    try {
      for (const file of Array.from(selected)) {
        await uploadFile(threadId, file)
      }
      await refresh()
      setNote('上传完成')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setUploading(false)
    }
  }

  /** 预览：文本直接读出来，图片给地址。 */
  const openPreview = async (file: WorkspaceFile) => {
    setError('')
    if (file.mime_type.startsWith('image/')) {
      setPreviewing(file)
      return
    }
    if (!isTextual(file) || file.size > MAX_PREVIEW) {
      setError('这个文件不适合在浏览器里预览 —— 用下载吧')
      return
    }
    try {
      const content = await readFileText(threadId, file.path)
      setEditing({ file, content })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  /** 保存改过的内容。 */
  const saveContent = async () => {
    if (!editing) return
    setBusy(true)
    try {
      await saveFile(threadId, editing.file.path, editing.content)
      setEditing(null)
      setNote(`已保存 ${editing.file.filename}`)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  /** 改名。 */
  const doRename = async () => {
    if (!renaming) return
    const target = newName.trim()
    if (!target) {
      setError('新名字不能为空')
      return
    }
    setBusy(true)
    try {
      await renameFile(threadId, renaming.path, target)
      setRenaming(null)
      setNote(`已改名为 ${target}`)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (file: WorkspaceFile) => {
    const ok = await confirm({
      title: `删掉「${file.path}」？`,
      description: '删了就找不回来了 —— 回收站是空的。',
      confirmText: '删掉',
      danger: true,
    })
    if (!ok) return
    try {
      await deleteFile(threadId, file.path)
      await refresh()
      setNote(`已删掉 ${file.filename}`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  /** 按路径过滤 —— 文件多了以后翻找比点开快。 */
  const shown = useMemo(() => {
    const keyword = filter.trim().toLowerCase()
    if (!keyword) return files
    return files.filter((file) => file.path.toLowerCase().includes(keyword))
  }, [files, filter])

  const columns: Column<WorkspaceFile>[] = [
    {
      header: '路径',
      mobile: 'title',
      render: (file) => (
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-ink">{file.path}</span>
          {file.uploaded ? <Badge tone="accent">上传件</Badge> : null}
        </span>
      ),
    },
    {
      header: '大小',
      mobile: 'meta',
      render: (file) => (
        <span className="text-xs tabular-nums text-ink-muted">{humanSize(file.size)}</span>
      ),
    },
    {
      header: '修改时间',
      mobile: 'meta',
      render: (file) => (
        <span className="text-xs text-ink-muted">{formatTime(file.modified_at)}</span>
      ),
    },
  ]

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6 sm:py-6">
      <PageHeader
        title="工作区文件"
        description="上传件与 agent 的产物都在这儿。平台产物（对话历史外置等）不在 —— 它对用户不可见。"
        actions={
          <>
            {files.length > 0 ? (
              <a
                href={archiveUrl(threadId)}
                className="inline-flex h-10 items-center rounded-lg border border-line px-4 text-sm font-medium text-ink-muted transition-colors hover:bg-hover hover:text-ink"
              >
                打包下载
              </a>
            ) : null}
            <label
              className={[
                'inline-flex h-10 cursor-pointer items-center rounded-lg bg-accent px-4 text-sm font-medium text-accent-text shadow-1 transition-colors hover:bg-accent-hover',
                uploading ? 'opacity-70' : '',
              ].join(' ')}
            >
              {uploading ? '上传中…' : '上传文件'}
              <input
                type="file"
                multiple
                className="hidden"
                onChange={(event) => void onUpload(event.target.files)}
              />
            </label>
          </>
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

      {files.length > 0 ? (
        <div className="mt-4 max-w-xs">
          <TextInput
            label=""
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="按路径过滤…"
          />
        </div>
      ) : null}

      <div className="mt-4 overflow-hidden rounded-xl border border-line bg-raised shadow-1">
        <DataTable
          columns={columns}
          rows={shown}
          rowKey={(file) => file.path}
          actions={(file) => (
            <>
              <Button size="sm" variant="ghost" onClick={() => void openPreview(file)}>
                查看
              </Button>
              {isTextual(file) && file.size <= MAX_PREVIEW ? (
                <>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      void readFileText(threadId, file.path).then((content) =>
                        setEditing({ file, content }),
                      )
                    }
                  >
                    编辑
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setRenaming(file)
                      setNewName(file.path)
                    }}
                  >
                    改名
                  </Button>
                </>
              ) : null}
              <a
                href={downloadUrl(threadId, file.path)}
                className="inline-flex h-7 items-center rounded-lg px-2.5 text-xs font-medium text-ink-faint transition-colors hover:bg-hover hover:text-ink"
              >
                下载
              </a>
              <Button size="sm" variant="ghost" onClick={() => void remove(file)}>
                <span className="text-danger">删除</span>
              </Button>
            </>
          )}
          empty={
            files.length === 0 ? (
              <EmptyState
                title="还没有文件"
                description="上传一个文件，或者让 agent 在工作区里生成产物 —— 它们都会出现在这儿。"
              />
            ) : (
              <EmptyState title="没有匹配的文件" description={`没有路径包含「${filter}」的文件。`} />
            )
          }
        />
      </div>

      {previewing ? (
        <Modal title={`预览：${previewing.filename}`} onClose={() => setPreviewing(null)} size="lg">
          <img
            src={downloadUrl(threadId, previewing.path)}
            alt={previewing.filename}
            className="mx-auto max-h-[70vh] rounded-lg"
          />
        </Modal>
      ) : null}

      {editing ? (
        <Modal
          title={`编辑：${editing.file.filename}`}
          onClose={() => setEditing(null)}
          size="lg"
          footer={
            <>
              <Button variant="ghost" onClick={() => setEditing(null)} disabled={busy}>
                取消
              </Button>
              <Button variant="primary" loading={busy} onClick={() => void saveContent()}>
                保存
              </Button>
            </>
          }
        >
          <textarea
            value={editing.content}
            onChange={(event) => setEditing({ ...editing, content: event.target.value })}
            spellCheck={false}
            className="h-[55vh] w-full resize-none rounded-lg border border-line bg-base p-3 font-mono text-xs leading-relaxed text-ink outline-none focus:border-accent focus:shadow-[0_0_0_3px_var(--ring)]"
          />
        </Modal>
      ) : null}

      {renaming ? (
        <Modal
          title={`改名：${renaming.filename}`}
          onClose={() => setRenaming(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setRenaming(null)} disabled={busy}>
                取消
              </Button>
              <Button variant="primary" loading={busy} onClick={() => void doRename()}>
                改名
              </Button>
            </>
          }
        >
          <TextInput
            label="新的虚拟路径"
            required
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            hint="以 / 开头，如 /notes/todo.md"
          />
        </Modal>
      ) : null}

      {confirmDialog}
    </div>
  )
}
