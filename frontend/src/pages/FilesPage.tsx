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
import Button from '../components/ui/Button'
import { TextInput } from '../components/ui/Fields'
import Modal from '../components/ui/Modal'
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
  const [files, setFiles] = useState<WorkspaceFile[]>([])
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [uploading, setUploading] = useState(false)
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
    if (!window.confirm(`删掉「${file.path}」？删了就找不回来了。`)) return
    try {
      await deleteFile(threadId, file.path)
      await refresh()
      setNote(`已删掉 ${file.filename}`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">工作区文件</h1>
          <p className="mt-1 text-sm text-ink-muted">
            上传件与 agent 的产物都在这儿。平台产物（对话历史外置等）不在 —— 它对用户不可见。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {files.length > 0 ? (
            <a
              href={archiveUrl(threadId)}
              className="rounded-lg border border-line px-3 py-1.5 text-xs text-ink-muted transition-colors hover:bg-hover hover:text-ink"
            >
              打包下载
            </a>
          ) : null}
          <label className="cursor-pointer rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-text transition-colors hover:bg-accent-hover">
            {uploading ? '上传中…' : '上传文件'}
            <input
              type="file"
              multiple
              className="hidden"
              onChange={(event) => void onUpload(event.target.files)}
            />
          </label>
        </div>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </div>
      )}
      {note && (
        <div className="mt-4 rounded-lg border border-success bg-success-soft px-3 py-2 text-sm text-success">
          {note}
        </div>
      )}

      <div className="mt-4 overflow-hidden rounded-lg border border-line bg-raised">
        {files.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-ink-faint">还没有文件</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-sunken text-left text-xs text-ink-faint">
              <tr>
                <th className="px-4 py-2 font-medium">路径</th>
                <th className="px-4 py-2 font-medium">大小</th>
                <th className="px-4 py-2 font-medium">修改时间</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {files.map((file) => (
                <tr key={file.path} className="border-t border-line">
                  <td className="px-4 py-2">
                    <span className="font-mono text-xs">{file.path}</span>
                    {file.uploaded && (
                      <span className="ml-2 rounded bg-accent-soft px-1.5 py-0.5 text-[10px] text-accent">
                        上传件
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{humanSize(file.size)}</td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{formatTime(file.modified_at)}</td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => void openPreview(file)}
                        className="text-xs text-ink-faint hover:text-ink"
                      >
                        查看
                      </button>
                      {isTextual(file) && file.size <= MAX_PREVIEW ? (
                        <>
                          <button
                            type="button"
                            onClick={() =>
                              void readFileText(threadId, file.path).then((content) =>
                                setEditing({ file, content }),
                              )
                            }
                            className="text-xs text-accent hover:underline"
                          >
                            编辑
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setRenaming(file)
                              setNewName(file.path)
                            }}
                            className="text-xs text-accent hover:underline"
                          >
                            改名
                          </button>
                        </>
                      ) : null}
                      <a
                        href={downloadUrl(threadId, file.path)}
                        className="text-xs text-ink-faint hover:text-ink"
                      >
                        下载
                      </a>
                      <button
                        type="button"
                        onClick={() => void remove(file)}
                        className="text-xs text-ink-faint hover:text-danger"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {previewing ? (
        <Modal title={`预览：${previewing.filename}`} onClose={() => setPreviewing(null)} wide>
          <img
            src={downloadUrl(threadId, previewing.path)}
            alt={previewing.filename}
            className="mx-auto max-h-[60vh] rounded-lg"
          />
        </Modal>
      ) : null}

      {editing ? (
        <Modal
          title={`编辑：${editing.file.filename}`}
          onClose={() => setEditing(null)}
          wide
          footer={
            <>
              <Button variant="ghost" onClick={() => setEditing(null)} disabled={busy}>
                取消
              </Button>
              <Button variant="primary" onClick={() => void saveContent()} disabled={busy}>
                {busy ? '保存中…' : '保存'}
              </Button>
            </>
          }
        >
          <textarea
            value={editing.content}
            onChange={(event) => setEditing({ ...editing, content: event.target.value })}
            spellCheck={false}
            className="h-[55vh] w-full resize-none rounded-lg border border-line bg-base p-3 font-mono text-xs leading-relaxed text-ink outline-none focus:border-accent"
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
              <Button variant="primary" onClick={() => void doRename()} disabled={busy}>
                {busy ? '改名中…' : '改名'}
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
    </div>
  )
}
