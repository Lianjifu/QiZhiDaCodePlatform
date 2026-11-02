/**
 * 工作区文件。
 *
 * 上传件与 agent 的产物都在这儿。附件是给对话用的 —— 点「带进对话」把
 * 虚拟路径塞进当前会话。
 *
 * **看的是当前会话的工作区** —— 文件按会话隔离，不是一个全局目录。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'

import { archiveUrl, deleteFile, downloadUrl, listFiles, uploadFile } from '../api/resources'
import { humanSize, formatTime } from '../lib/format'
import { useSessions } from '../features/sessions/store'
import type { WorkspaceFile } from '../types/api'

export default function FilesPage() {
  const [files, setFiles] = useState<WorkspaceFile[]>([])
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [searchParams] = useSearchParams()
  const { sessions } = useSessions()

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
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setUploading(false)
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

      {error && <div className="mt-4 rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger">{error}</div>}

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
                    <a
                      href={downloadUrl(threadId, file.path)}
                      className="text-xs text-accent hover:underline"
                    >
                      下载
                    </a>
                    <button
                      type="button"
                      onClick={async () => {
                        await deleteFile(threadId, file.path)
                        await refresh()
                      }}
                      className="ml-3 text-xs text-ink-faint hover:text-danger"
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
