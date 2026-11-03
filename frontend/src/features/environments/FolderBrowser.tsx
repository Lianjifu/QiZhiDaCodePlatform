/**
 * 目录浏览器：挑一个本机目录当工作目录。
 *
 * **为什么不是一个下拉框**：白名单下可能有几十层目录，固定的下拉列表挑不动。
 * 这里能往下钻（点子目录进去）、往回退（上一级），也能直接把路径粘进来
 * （在访达里按 ⌘⌥C 复制路径，回车直接跳过去）。
 *
 * **只能看白名单里的目录** —— 目录浏览也是读宿主机的文件系统，白名单是
 * 唯一的闸门（后端解析真实路径之后比较，软链接指向外面的一律拒绝）。
 */
import { useCallback, useEffect, useState } from 'react'

import { browseEnvironments } from '../../api/resources'
import { BranchIcon, ChevronIcon, FolderIcon } from '../../components/icons'
import type { BrowseResult } from '../../types/api'

export default function FolderBrowser({
  value,
  onChange,
}: {
  /** 选中的目录 */
  value: string
  onChange: (path: string) => void
}) {
  const [cursor, setCursor] = useState<BrowseResult | null>(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const apply = useCallback((result: BrowseResult) => {
    setCursor(result)
    setInput(result.path)
    setError('')
  }, [])

  const browse = useCallback(
    async (path?: string) => {
      setLoading(true)
      try {
        apply(await browseEnvironments(path))
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setLoading(false)
      }
    },
    [apply],
  )

  useEffect(() => {
    let cancelled = false
    void browseEnvironments()
      .then((result) => {
        if (!cancelled) apply(result)
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      })
    return () => {
      cancelled = true
    }
  }, [apply])

  const enter = (path: string) => {
    setInput(path)
    void browse(path)
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => cursor?.parent && enter(cursor.parent)}
          disabled={!cursor?.parent}
          className="shrink-0 rounded-lg border border-line px-2 py-1 text-xs text-ink-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-40"
          title="上一级"
        >
          上一级
        </button>
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void browse(input.trim())
          }}
          placeholder="绝对路径，回车跳过去"
          className="min-w-0 flex-1 rounded-lg border border-line bg-base px-2.5 py-1 font-mono text-xs text-ink outline-none transition-colors placeholder:font-sans placeholder:text-ink-faint focus:border-accent focus:shadow-[0_0_0_3px_var(--ring)]"
        />
      </div>

      <div className="max-h-56 overflow-y-auto rounded-lg border border-line">
        {loading ? (
          <div className="px-3 py-4 text-center text-xs text-ink-faint">读目录…</div>
        ) : (cursor?.entries.length ?? 0) === 0 ? (
          <div className="px-3 py-4 text-center text-xs text-ink-faint">这个目录下没有子目录</div>
        ) : (
          <ul className="divide-y divide-line">
            {cursor?.entries.map((entry) => (
              <li key={entry.path}>
                <button
                  type="button"
                  onClick={() => enter(entry.path)}
                  className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors hover:bg-hover"
                >
                  <span className="shrink-0 text-ink-faint">
                    {entry.is_repo ? (
                      <BranchIcon className="h-3.5 w-3.5" />
                    ) : (
                      <FolderIcon className="h-3.5 w-3.5" />
                    )}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">{entry.name}</span>
                  <ChevronIcon className="h-3.5 w-3.5 shrink-0 text-ink-faint" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {error ? <div className="text-[11px] text-danger">{error}</div> : null}

      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-[11px] text-ink-faint">
          {value || '还没选目录'}
        </span>
        <button
          type="button"
          onClick={() => cursor && onChange(cursor.path)}
          disabled={!cursor}
          className="shrink-0 rounded-lg border border-line px-2.5 py-1 text-xs text-ink-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-40"
        >
          用这个目录
        </button>
      </div>
    </div>
  )
}
