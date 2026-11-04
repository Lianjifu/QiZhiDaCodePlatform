/**
 * 输入框。
 *
 * 空状态时**居中**（像 Claude 桌面端那样），有消息之后固定在底部。
 * 底部一行：`+`（附件 / 斜杠命令 / @ 提及）、权限模式、模型选择。
 *
 * ## 三种输入能力
 *
 * | 能力 | 怎么用 | 落到哪 |
 * | --- | --- | --- |
 * | 添加文件或图片 | `+` 菜单 | 上传到会话工作区；图片**内联**进这一轮的消息，模型当场看得见 |
 * | 斜杠命令 | 敲 `/`，或 `+` 菜单里点 | prompt 类填一段提示词，action 类触发本地动作 |
 * | @ 提及 | 敲 `@`，或 `+` 菜单里点 | 路径写进正文；**被提及的图片会自动带成附件** |
 *
 * 触发符的识别规则在 {@link detectTrigger} —— 看光标所在的**那个词**：
 * 以 `@` 开头是提及，以 `/` 开头是命令。所以 ``/uploads/x.png`` 这种路径
 * 不会被当成斜杠命令。
 */
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'

import { listCommands, listProviders, listWorkspaceTree, uploadFile } from '../../api/resources'
import { ArrowUpIcon, AtIcon, PaperclipIcon, PlusIcon, SlashIcon, StopIcon } from '../../components/icons'
import { useConfirm } from '../../components/ui/useConfirm'
import { humanSize } from '../../lib/format'
import type { LLMProvider, SlashCommand, WorkspaceFile } from '../../types/api'
import ContextUsageIndicator from '../sessions/ContextUsageIndicator'
import MentionPicker from './MentionPicker'
import PermissionModePicker from './PermissionModePicker'
import SlashCommandMenu from './SlashCommandMenu'
import { detectTrigger, mentionedPaths, orderEntries, type Trigger } from './triggers'

export interface Attachment {
  path: string
  filename: string
  size: number
  isImage: boolean
}

/** 外层（空状态页的推荐问题）能命令输入框做点事。 */
export interface ComposerHandle {
  /** 填一段文字并聚焦 —— 推荐问题点了用这个 */
  setText: (text: string) => void
}

export default forwardRef<ComposerHandle, {
  threadId: string
  centered?: boolean
  busy: boolean
  disabled?: boolean
  onSend: (text: string, attachments: string[], providerId: number | null) => void
  onStop: () => void
  /** 本地动作（开新会话 / 清空 / 改名）交给外层处理 */
  onCommandAction?: (action: string) => void
  /** 打开右侧的完整状态面板 */
  onOpenDetail?: () => void
}>(function Composer(
  {
    threadId,
    centered,
    busy,
    disabled,
    onSend,
    onStop,
    onCommandAction,
    onOpenDetail,
  },
  ref,
) {
  const [text, setText] = useState('')
  /**
   * 附件**记着它是在哪个会话上传的**。
   *
   * 附件落在会话工作区里 —— 带着上一个会话的附件去另一个会话发，后端
   * stat 不到那个路径，会静默跳过，图片就这么没了（用户还以为发出去了）。
   * 记着会话号，换会话时自然就看不见它们了。
   */
  const [attachments, setAttachments] = useState<{ threadId: string; items: Attachment[] }>({
    threadId,
    items: [],
  })
  const visibleAttachments = attachments.threadId === threadId ? attachments.items : []
  const [uploading, setUploading] = useState(false)
  const [providers, setProviders] = useState<LLMProvider[]>([])
  const [providerId, setProviderId] = useState<number | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [plusOpen, setPlusOpen] = useState(false)
  const [contextOpen, setContextOpen] = useState(false)
  const [commands, setCommands] = useState<SlashCommand[]>([])
  const [tree, setTree] = useState<WorkspaceFile[]>([])
  const [treeLoading, setTreeLoading] = useState(false)
  const [trigger, setTrigger] = useState<Trigger | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const { confirm, dialog: confirmDialog } = useConfirm()

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const pickerRef = useRef<HTMLDivElement>(null)
  const plusMenuRef = useRef<HTMLDivElement>(null)
  const shellRef = useRef<HTMLDivElement>(null)

  // 模型清单
  useEffect(() => {
    let cancelled = false
    listProviders()
      .then((page) => {
        if (cancelled) return
        setProviders(page.items.filter((item) => item.is_active))
        const fallback = page.items.find((item) => item.is_default) ?? page.items[0]
        if (fallback) setProviderId(fallback.id)
      })
      .catch(() => {
        // 模型清单拿不到就只做纯文本对话，不挡路
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 斜杠命令清单
  useEffect(() => {
    let cancelled = false
    listCommands()
      .then((items) => {
        if (!cancelled) setCommands(items)
      })
      .catch(() => {
        // 拿不到就没有斜杠命令，不挡路
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 点外面收起浮层
  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node
      if (pickerOpen && !pickerRef.current?.contains(target)) setPickerOpen(false)
      if (plusOpen && !plusMenuRef.current?.contains(target)) setPlusOpen(false)
      if (contextOpen && !shellRef.current?.contains(target)) setContextOpen(false)
      if (trigger && !shellRef.current?.contains(target)) setTrigger(null)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [pickerOpen, plusOpen, contextOpen, trigger])

  // 输入框随内容长高
  useEffect(() => {
    const element = textareaRef.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 240)}px`
  }, [text])

  const loadTree = useCallback(async (): Promise<WorkspaceFile[]> => {
    setTreeLoading(true)
    try {
      const result = await listWorkspaceTree(threadId)
      setTree(result.files)
      return result.files
    } catch {
      setTree([])
      return []
    } finally {
      setTreeLoading(false)
    }
  }, [threadId])

  // 打开 @ 面板时读一次工作区 —— agent 可能刚建了新文件
  const openMention = useCallback(() => {
    void loadTree()
  }, [loadTree])

  const filteredCommands = useMemo(() => {
    if (trigger?.kind !== 'slash') return []
    const query = trigger.query.toLowerCase()
    return commands.filter(
      (item) =>
        item.name.toLowerCase().includes(query) ||
        item.description.toLowerCase().includes(query),
    )
  }, [commands, trigger])

  const filteredEntries = useMemo(() => {
    if (trigger?.kind !== 'mention') return []
    const query = trigger.query.toLowerCase()
    const matched = query
      ? tree.filter(
          (item) =>
            item.path.toLowerCase().includes(query) ||
            item.filename.toLowerCase().includes(query),
        )
      : tree
    return orderEntries(matched)
  }, [tree, trigger])

  /** 把光标放到某处并聚焦。 */
  const focusCaret = (position: number) => {
    requestAnimationFrame(() => {
      const element = textareaRef.current
      if (!element) return
      element.focus()
      element.setSelectionRange(position, position)
    })
  }

  // 外层要能往输入框里塞文字（空状态页的推荐问题）
  useImperativeHandle(
    ref,
    () => ({
      setText: (value: string) => {
        setText(value)
        setTrigger(null)
        focusCaret(value.length)
      },
    }),
    [],
  )

  /** 在光标处插入触发符，并打开对应的面板。 */
  const openTrigger = (kind: 'slash' | 'mention') => {
    const element = textareaRef.current
    const caret = element?.selectionStart ?? text.length
    const char = kind === 'slash' ? '/' : '@'
    // **触发符前面得是词的开头** —— 识别规则看的是"光标所在的词"，
    // 在词中间插一个 @ 的话，下一次输入算出来的词不以 @ 开头，面板秒关。
    const before = caret > 0 ? text[caret - 1] : ''
    const needsSpace = before !== '' && !/\s/.test(before)
    const insert = needsSpace ? ` ${char}` : char
    setText(text.slice(0, caret) + insert + text.slice(caret))
    setTrigger({ kind, query: '', start: caret + (needsSpace ? 1 : 0), end: caret + insert.length })
    setActiveIndex(0)
    if (kind === 'mention') openMention()
    focusCaret(caret + insert.length)
  }

  const runAction = (action: string) => {
    if (action === 'model') {
      setPickerOpen(true)
      return
    }
    if (action === 'mention') {
      openTrigger('mention')
      return
    }
    onCommandAction?.(action)
  }

  const selectCommand = (command: SlashCommand) => {
    if (!trigger) return
    if (command.kind === 'action') {
      // 命令本身不吃掉 —— 把触发的那段从正文里删掉
      setText(text.slice(0, trigger.start) + text.slice(trigger.end))
      setTrigger(null)
      runAction(command.action)
      return
    }
    // prompt 类：把命令名换成提示词，用户改完再发
    const next = text.slice(0, trigger.start) + command.template + text.slice(trigger.end)
    setText(next)
    setTrigger(null)
    focusCaret(trigger.start + command.template.length)
  }

  const selectEntry = (entry: WorkspaceFile) => {
    if (!trigger) return
    const insertion = `@${entry.path} `
    const next = text.slice(0, trigger.start) + insertion + text.slice(trigger.end)
    setText(next)
    setTrigger(null)
    focusCaret(trigger.start + insertion.length)
  }

  const canSend = text.trim().length > 0 && !busy && !disabled

  const submit = async () => {
    if (!canSend) return
    const merged = [...visibleAttachments]
    const paths = merged.map((item) => item.path)
    // 正文里 @ 到的图片，自动带成附件 —— 模型这一轮才看得见
    const mentioned = mentionedPaths(text)
    if (mentioned.length > 0) {
      const entries = tree.length > 0 ? tree : await loadTree()
      for (const path of mentioned) {
        const entry = entries.find((item) => item.path === path)
        if (!entry || entry.is_dir || !entry.mime_type.startsWith('image/')) continue
        if (paths.includes(path)) continue
        paths.push(path)
        merged.push({
          path,
          filename: entry.filename,
          size: entry.size,
          isImage: true,
        })
      }
    }
    onSend(text, paths, providerId)
    setText('')
    setAttachments({ threadId, items: [] })
    setTrigger(null)
  }

  const pickFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setUploading(true)
    const failed: string[] = []
    try {
      for (const file of Array.from(files)) {
        try {
          const uploaded = await uploadFile(threadId, file)
          const item: Attachment = {
            path: uploaded.path,
            filename: uploaded.original_name || uploaded.filename,
            size: uploaded.size,
            isImage: uploaded.is_image,
          }
          setAttachments((prev) => ({
            threadId,
            items: prev.threadId === threadId ? [...prev.items, item] : [item],
          }))
        } catch (cause) {
          // **一个失败不挡后面的** —— 之前直接 break 出循环，剩下的文件
          // 悄悄就没了，用户还以为都传上去了。
          failed.push(`${file.name}：${cause instanceof Error ? cause.message : String(cause)}`)
        }
      }
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
    if (failed.length > 0) {
      // **不用 window.alert** —— 原生框的样式跟平台没关系，还会阻塞整页。
      await confirm({
        title: '有文件没传上去',
        description: `有 ${failed.length} 个文件没传上去：${failed.join('；')}`,
        confirmText: '知道了',
        cancelText: '关闭',
      })
    }
  }

  const currentProvider = providers.find((item) => item.id === providerId)

  const optionCount =
    trigger?.kind === 'slash' ? filteredCommands.length : filteredEntries.length

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (trigger) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        if (optionCount === 0) return
        const delta = event.key === 'ArrowDown' ? 1 : -1
        setActiveIndex((index) => (index + delta + optionCount) % optionCount)
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setTrigger(null)
        return
      }
      if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
        // **没有匹配项时照常发送** —— 否则输入「/不存在」之后回车永远发不出去，
        // 用户得先把触发符删掉才能发这条消息。
        const picked =
          trigger.kind === 'slash' ? filteredCommands[activeIndex] : filteredEntries[activeIndex]
        if (!picked) {
          setTrigger(null)
          event.preventDefault()
          void submit()
          return
        }
        event.preventDefault()
        if (trigger.kind === 'slash') selectCommand(picked as SlashCommand)
        else selectEntry(picked as WorkspaceFile)
        return
      }
    }
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void submit()
    }
  }

  return (
    <div
      className={
        centered ? 'mx-auto w-full max-w-4xl px-6' : 'mx-auto w-full max-w-4xl px-6 pb-6'
      }
    >
      <div ref={shellRef} className="relative">
        {/* 浮层：斜杠命令面板 / @ 提及面板 */}
        {trigger?.kind === 'slash' ? (
          <SlashCommandMenu
            commands={filteredCommands}
            activeIndex={activeIndex}
            onHover={setActiveIndex}
            onSelect={selectCommand}
          />
        ) : null}
        {trigger?.kind === 'mention' ? (
          <MentionPicker
            entries={filteredEntries}
            activeIndex={activeIndex}
            loading={treeLoading}
            onHover={setActiveIndex}
            onSelect={selectEntry}
          />
        ) : null}

        <div
          className={[
            'rounded-3xl border border-line bg-raised transition-[border-color,box-shadow]',
            // 聚焦时透出品牌色 —— 输入框是页面上最该有"焦点"的东西
            'focus-within:border-accent/50 focus-within:shadow-[0_0_0_3px_var(--ring)]',
            centered
              ? 'shadow-3'
              : 'shadow-2',
          ].join(' ')}
        >
          {visibleAttachments.length > 0 && (
            <div className="flex flex-wrap gap-2 px-3 pt-3">
              {visibleAttachments.map((item) => (
                <span
                  key={item.path}
                  className="flex items-center gap-1.5 rounded-lg bg-sunken px-2 py-1 text-xs text-ink-muted"
                >
                  {item.isImage ? '🖼' : '📄'} {item.filename}
                  <span className="text-ink-faint">{humanSize(item.size)}</span>
                  <button
                    type="button"
                    onClick={() =>
                      setAttachments((prev) => ({
                        threadId,
                        items: prev.items.filter((entry) => entry.path !== item.path),
                      }))
                    }
                    className="text-ink-faint hover:text-ink"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}

          <textarea
            ref={textareaRef}
            value={text}
            disabled={disabled}
            onChange={(event) => {
              const value = event.target.value
              const caret = event.target.selectionStart ?? value.length
              const next = detectTrigger(value, caret)
              setText(value)
              setTrigger(next)
              // 过滤词变了就回到第一项
              setActiveIndex(0)
              // 刚敲出 @ —— 读一次工作区（agent 可能刚建了新文件）
              if (next?.kind === 'mention' && trigger?.kind !== 'mention') openMention()
            }}
            onKeyDown={onKeyDown}
            placeholder="问点什么…（/ 斜杠命令 · @ 提及文件）"
            rows={1}
            className={[
              'max-h-60 w-full resize-none bg-transparent pb-1 text-sm text-ink outline-none',
              'placeholder:text-ink-faint disabled:opacity-50',
              centered ? 'px-5 pt-4' : 'px-4 pt-3.5',
            ].join(' ')}
          />

          <div className="flex items-center gap-1 px-2.5 pb-2.5">
            <input
              ref={fileRef}
              type="file"
              multiple
              className="hidden"
              onChange={(event) => void pickFiles(event.target.files)}
            />
            {/* + 菜单 */}
            <div ref={plusMenuRef} className="relative">
              <button
                type="button"
                title="添加文件、斜杠命令或提及"
                disabled={disabled || uploading}
                onClick={() => {
                  setPlusOpen((value) => !value)
                  setContextOpen(false)
                  setTrigger(null)
                }}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-50"
              >
                {uploading ? '…' : <PlusIcon className="h-4 w-4" />}
              </button>
              {plusOpen && (
                <div className="absolute bottom-full left-0 z-20 mb-2 w-56 overflow-hidden rounded-xl border border-line bg-overlay py-1 shadow-xl">
                  <button
                    type="button"
                    onClick={() => {
                      setPlusOpen(false)
                      fileRef.current?.click()
                    }}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-ink transition-colors hover:bg-hover"
                  >
                    <PaperclipIcon className="h-4 w-4 shrink-0 text-ink-faint" />
                    添加文件或图片
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setPlusOpen(false)
                      openTrigger('slash')
                    }}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-ink transition-colors hover:bg-hover"
                  >
                    <SlashIcon className="h-4 w-4 shrink-0 text-ink-faint" />
                    斜杠命令
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setPlusOpen(false)
                      openTrigger('mention')
                    }}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-ink transition-colors hover:bg-hover"
                  >
                    <AtIcon className="h-4 w-4 shrink-0 text-ink-faint" />
                    提及文件或目录
                  </button>
                </div>
              )}
            </div>

            {/* 权限模式：这一轮要不要人批 */}
            <PermissionModePicker threadId={threadId} />

            <div className="flex-1" />

            {/* 上下文占用：在模型选择的左边，点开看明细 */}
            <ContextUsageIndicator
              threadId={threadId}
              busy={busy}
              open={contextOpen}
              onToggle={() => {
                setContextOpen((value) => !value)
                setPickerOpen(false)
              }}
              onOpenDetail={onOpenDetail}
            />

            {/* 模型选择 */}
            <div ref={pickerRef} className="relative">
              <button
                type="button"
                onClick={() => {
                  setPickerOpen((value) => !value)
                  setContextOpen(false)
                }}
                className="rounded-lg px-2.5 py-1.5 text-xs text-ink-muted transition-colors hover:bg-hover hover:text-ink"
              >
                {currentProvider?.default_model ?? '选择模型'} ▾
              </button>
              {pickerOpen && (
                <div className="absolute bottom-full right-0 mb-1 w-56 overflow-hidden rounded-lg border border-line bg-overlay shadow-lg">
                  {providers.length === 0 ? (
                    <div className="px-3 py-2 text-xs text-ink-faint">没有可用的模型配置</div>
                  ) : (
                    providers.map((provider) => (
                      <button
                        key={provider.id}
                        type="button"
                        onClick={() => {
                          setProviderId(provider.id)
                          setPickerOpen(false)
                        }}
                        className={[
                          'flex w-full flex-col items-start px-3 py-2 text-left transition-colors hover:bg-hover',
                          provider.id === providerId ? 'text-ink' : 'text-ink-muted',
                        ].join(' ')}
                      >
                        <span className="text-xs font-medium">{provider.default_model}</span>
                        <span className="text-[11px] text-ink-faint">
                          {provider.name}
                          {provider.vision_enabled ? ' · 支持看图' : ''}
                        </span>
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>

            {busy ? (
              <button
                type="button"
                title="停止"
                onClick={onStop}
                className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-accent-text transition-transform hover:opacity-90"
              >
                <StopIcon className="h-3.5 w-3.5" />
              </button>
            ) : (
              <button
                type="button"
                title="发送"
                disabled={!canSend}
                onClick={() => void submit()}
                className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-accent-text transition-colors hover:bg-accent-hover disabled:opacity-25"
              >
                <ArrowUpIcon className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>
      </div>
      {confirmDialog}
    </div>
  )
})
