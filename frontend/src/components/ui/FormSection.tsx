/**
 * 表单里的一段。
 *
 * 一长条表单不分段就很难扫 —— 标题、参数、权限混在一起。分段之后每一段
 * 只管一件事，填的时候知道自己在填哪部分。
 */
import type { ReactNode } from 'react'

export default function FormSection({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <section>
      <h3 className="text-xs font-medium tracking-wide text-ink-muted">{title}</h3>
      {description ? (
        <p className="mt-0.5 text-xs leading-relaxed text-ink-faint">{description}</p>
      ) : null}
      <div className="mt-3 space-y-3.5">{children}</div>
    </section>
  )
}
