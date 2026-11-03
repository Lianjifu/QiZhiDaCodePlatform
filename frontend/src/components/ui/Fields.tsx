/**
 * 表单控件。
 *
 * 一套标签、提示、错误提示的排版，页面里别各写各的。
 */
import { useState, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'

/** 输入框统一样式 —— 只在这个文件里定义，不导出去（免得触发"只导出组件"那条规则）。 */
const CONTROL =
  'w-full rounded-lg border border-line bg-base px-2.5 py-1.5 text-sm text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent disabled:opacity-60'

/** 一个带标签的控件。 */
export function Field({
  label,
  hint,
  required,
  error,
  children,
}: {
  label: string
  hint?: string
  required?: boolean
  error?: string
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink-muted">
        {label}
        {required ? <span className="ml-0.5 text-danger">*</span> : null}
      </span>
      {children}
      {error ? (
        <span className="mt-1 block text-[11px] text-danger">{error}</span>
      ) : hint ? (
        <span className="mt-1 block text-[11px] text-ink-faint">{hint}</span>
      ) : null}
    </label>
  )
}

/** 单行输入。 */
export function TextInput({
  label,
  hint,
  required,
  error,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & {
  label: string
  hint?: string
  required?: boolean
  error?: string
}) {
  return (
    <Field label={label} hint={hint} required={required} error={error}>
      <input {...props} className={CONTROL} />
    </Field>
  )
}

/** 多行输入。 */
export function TextArea({
  label,
  hint,
  required,
  error,
  rows = 4,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label: string
  hint?: string
  required?: boolean
  error?: string
}) {
  return (
    <Field label={label} hint={hint} required={required} error={error}>
      <textarea {...props} rows={rows} className={`${CONTROL} resize-y leading-relaxed`} />
    </Field>
  )
}

/** 下拉。 */
export function Select({
  label,
  hint,
  required,
  error,
  options,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & {
  label: string
  hint?: string
  required?: boolean
  error?: string
  options: { value: string; label: string }[]
}) {
  return (
    <Field label={label} hint={hint} required={required} error={error}>
      <select {...props} className={CONTROL}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </Field>
  )
}

/** 开关（勾选框 + 说明）。 */
export function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string
  hint?: string
  checked: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 accent-accent"
      />
      <span>
        <span className="block text-xs font-medium text-ink-muted">{label}</span>
        {hint ? <span className="block text-[11px] text-ink-faint">{hint}</span> : null}
      </span>
    </label>
  )
}

/** 逗号分隔的标签列表。 */
export function TagsField({
  label,
  hint,
  value,
  onChange,
}: {
  label: string
  hint?: string
  value: string[]
  onChange: (value: string[]) => void
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        value={value.join(', ')}
        onChange={(event) =>
          onChange(
            event.target.value
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean),
          )
        }
        placeholder="用逗号隔开，如：python, 测试"
        className={CONTROL}
      />
    </Field>
  )
}

/** 一行一条的列表（检查项、示例那种）。 */
export function LinesField({
  label,
  hint,
  value,
  onChange,
  rows = 4,
}: {
  label: string
  hint?: string
  value: string[]
  onChange: (value: string[]) => void
  rows?: number
}) {
  return (
    <Field label={label} hint={hint}>
      <textarea
        rows={rows}
        value={value.join('\n')}
        onChange={(event) => onChange(event.target.value.split('\n'))}
        placeholder="一行一条"
        className={`${CONTROL} resize-y leading-relaxed`}
      />
    </Field>
  )
}

/**
 * JSON 编辑框。
 *
 * **边填边验**：填的不是合法 JSON 就当场标红 —— 免得提交了才被后端拒。
 *
 * 内部存的是**文本**，往外抛的是解析后的对象；解析不过就还留着上一个
 * 合法值（不抛错，也不把坏数据递给后端）。
 */
export function JsonField({
  label,
  hint,
  value,
  onChange,
  rows = 5,
  placeholder = '{}',
}: {
  label: string
  hint?: string
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
  rows?: number
  placeholder?: string
}) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2))
  let error = ''
  try {
    const parsed = JSON.parse(text || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      error = '要的是一个 JSON 对象'
    }
  } catch {
    error = '不是合法的 JSON'
  }

  return (
    <Field label={label} hint={hint} error={error || undefined}>
      <textarea
        rows={rows}
        value={text}
        placeholder={placeholder}
        onChange={(event) => {
          const next = event.target.value
          setText(next)
          try {
            const parsed = JSON.parse(next || '{}')
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
              onChange(parsed as Record<string, unknown>)
            }
          } catch {
            // 不是合法 JSON —— 保持上一次的合法值，等用户改对
          }
        }}
        className={`${CONTROL} resize-y font-mono text-xs leading-relaxed`}
      />
    </Field>
  )
}
