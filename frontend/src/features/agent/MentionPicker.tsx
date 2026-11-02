/**
 * @ 提及面板：挑工作区里的文件或目录。
 *
 * 选中的项会以 ``@/路径`` 的形式插进输入框。**图片会被自动带成附件** ——
 * 模型这一轮就能看见它（见 Composer 里的转交逻辑）。
 */
import { Fragment } from 'react'

import { AtIcon, FileIcon, FolderIcon } from '../../components/icons'
import { humanSize } from '../../lib/format'
import type { WorkspaceFile } from '../../types/api'

export default function MentionPicker({
  entries,
  activeIndex,
  loading,
  onHover,
  onSelect,
}: {
  entries: WorkspaceFile[]
  activeIndex: number
  loading?: boolean
  onHover: (index: number) => void
  onSelect: (entry: WorkspaceFile) => void
}) {
  // entries 已经排好序（文件在前）—— 下标直接用
  const filesEnd = entries.filter((item) => !item.is_dir).length

  return (
    <div className="absolute bottom-full left-0 z-20 mb-2 w-[min(30rem,90vw)] overflow-hidden rounded-xl border border-line bg-overlay shadow-xl">
      <div className="max-h-72 overflow-y-auto py-1">
        {loading ? (
          <div className="px-3 py-2 text-xs text-ink-faint">正在读工作区…</div>
        ) : entries.length === 0 ? (
          <div className="px-3 py-2 text-xs text-ink-faint">
            工作区还是空的 —— 先上传点文件，或让 agent 建一个。
          </div>
        ) : (
          <>
            {filesEnd > 0 && (
              <div className="px-3 pb-0.5 pt-1 text-[11px] font-medium tracking-wide text-ink-faint">
                文件
              </div>
            )}
            {entries.map((entry, index) => (
              <Fragment key={entry.path}>
                {index === filesEnd && filesEnd < entries.length && (
                  <div className="px-3 pb-0.5 pt-1.5 text-[11px] font-medium tracking-wide text-ink-faint">
                    目录
                  </div>
                )}
                <button
                  type="button"
                  onMouseEnter={() => onHover(index)}
                  onClick={() => onSelect(entry)}
                  className={[
                    'flex w-full items-center gap-2.5 px-3 py-1.5 text-left transition-colors',
                    index === activeIndex ? 'bg-hover' : '',
                  ].join(' ')}
                >
                  {entry.is_dir ? (
                    <FolderIcon className="h-3.5 w-3.5 shrink-0 text-ink-faint" />
                  ) : (
                    <FileIcon className="h-3.5 w-3.5 shrink-0 text-ink-faint" />
                  )}
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">{entry.path}</span>
                  {!entry.is_dir && entry.size > 0 ? (
                    <span className="shrink-0 text-[11px] text-ink-faint">
                      {humanSize(entry.size)}
                    </span>
                  ) : null}
                </button>
              </Fragment>
            ))}
          </>
        )}
      </div>
      <div className="flex items-center gap-1.5 border-t border-line px-3 py-1.5 text-[11px] text-ink-faint">
        <AtIcon className="h-3 w-3" />
        <span>↑↓ 选择 · Enter 确认 · Esc 关闭</span>
      </div>
    </div>
  )
}
