/**
 * 输入框。
 *
 * 空状态时**居中**（像 Claude 桌面端那样），有消息之后固定在底部。
 * 底部一行：`+`（附件）、模式（只留「对话」一档）、模型选择。
 */
import { useEffect, useRef, useState } from 'react'

import { listProviders, uploadFile } from '../../api/resources'
import { humanSize } from '../../lib/format'
import type { LLMProvider } from '../../types/api'

export interface Attachment {
  path: string
  filename: string
  size: number
  isImage: boolean
}

export default function Composer({
  threadId,
  centered,
  busy,
  disabled,
  onSend,
  onStop,
}: {
  threadId: string
  centered?: boolean
  busy: boolean
  disabled?: boolean
  onSend: (text: string, attachments: string[], providerId: number | null) => void
  onStop: () => void
}) {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [providers, setProviders] = useState<LLMProvider[]>([])
  const [providerId, setProviderId] = useState<number | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const pickerRef = useRef<HTMLDivElement>(null)

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

  // 点外面收起模型下拉
  useEffect(() => {
    if (!pickerOpen) return
    const onPointerDown = (event: MouseEvent) => {
      if (!pickerRef.current?.contains(event.target as Node)) setPickerOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [pickerOpen])

  // 输入框随内容长高
  useEffect(() => {
    const element = textareaRef.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 240)}px`
  }, [text])

  const canSend = text.trim().length > 0 && !busy && !disabled

  const submit = () => {
    if (!canSend) return
    onSend(text, attachments.map((item) => item.path), providerId)
    setText('')
    setAttachments([])
  }

  const pickFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setUploading(true)
    try {
      for (const file of Array.from(files)) {
        const uploaded = await uploadFile(threadId, file)
        setAttachments((prev) => [
          ...prev,
          {
            path: uploaded.path,
            filename: uploaded.original_name || uploaded.filename,
            size: uploaded.size,
            isImage: uploaded.is_image,
          },
        ])
      }
    } catch (cause) {
      window.alert(`上传失败：${cause instanceof Error ? cause.message : String(cause)}`)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const currentProvider = providers.find((item) => item.id === providerId)

  return (
    <div className={centered ? 'mx-auto w-full max-w-3xl px-6' : 'mx-auto w-full max-w-3xl px-6 pb-6'}>
      <div className="rounded-2xl border border-line bg-raised shadow-sm">
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 px-3 pt-3">
            {attachments.map((item) => (
              <span
                key={item.path}
                className="flex items-center gap-1.5 rounded-lg bg-sunken px-2 py-1 text-xs text-ink-muted"
              >
                {item.isImage ? '🖼' : '📄'} {item.filename}
                <span className="text-ink-faint">{humanSize(item.size)}</span>
                <button
                  type="button"
                  onClick={() =>
                    setAttachments((prev) => prev.filter((entry) => entry.path !== item.path))
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
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              submit()
            }
          }}
          placeholder="输入 / 使用 Skills"
          rows={1}
          className="max-h-60 w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-sm text-ink outline-none placeholder:text-ink-faint disabled:opacity-50"
        />

        <div className="flex items-center gap-1 px-2.5 pb-2.5">
          <input
            ref={fileRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => void pickFiles(event.target.files)}
          />
          <button
            type="button"
            title="添加附件"
            disabled={disabled || uploading}
            onClick={() => fileRef.current?.click()}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-50"
          >
            {uploading ? '…' : '+'}
          </button>

          {/* 模式：目前只有「对话」一档 */}
          <span className="rounded-lg bg-sunken px-2.5 py-1.5 text-xs text-ink">对话</span>

          <div className="flex-1" />

          {/* 模型选择 */}
          <div ref={pickerRef} className="relative">
            <button
              type="button"
              onClick={() => setPickerOpen((value) => !value)}
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
              onClick={onStop}
              className="rounded-lg border border-line px-3 py-1.5 text-xs text-ink-muted transition-colors hover:bg-hover"
            >
              停止
            </button>
          ) : (
            <button
              type="button"
              disabled={!canSend}
              onClick={submit}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-text transition-colors hover:bg-accent-hover disabled:opacity-40"
            >
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
