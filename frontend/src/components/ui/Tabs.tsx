/**
 * 标签页。
 *
 * **窄屏上可以横向滑** —— 标签多了挤在一行会互相压，滑比换行自然。
 */
import type { ReactNode } from 'react'

export interface TabItem<T extends string> {
  key: T
  label: string
  /** 右上角的数量角标 */
  count?: number
  icon?: ReactNode
}

export default function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: TabItem<T>[]
  value: T
  onChange: (key: T) => void
}) {
  return (
    <div className="-mx-1 overflow-x-auto px-1">
      <div role="tablist" className="flex min-w-max gap-1 border-b border-line">
        {tabs.map((item) => {
          const active = item.key === value
          return (
            <button
              key={item.key}
              role="tab"
              type="button"
              aria-selected={active}
              onClick={() => onChange(item.key)}
              className={[
                'relative -mb-px flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm transition-colors',
                active
                  ? 'border-accent font-medium text-ink'
                  : 'border-transparent text-ink-muted hover:text-ink',
              ].join(' ')}
            >
              {item.icon ? <span className="text-ink-faint">{item.icon}</span> : null}
              {item.label}
              {item.count !== undefined ? (
                <span
                  className={[
                    'rounded-md px-1.5 py-px text-[10px] font-medium tabular-nums',
                    active ? 'bg-accent-soft text-accent' : 'bg-sunken text-ink-faint',
                  ].join(' ')}
                >
                  {item.count}
                </span>
              ) : null}
            </button>
          )
        })}
      </div>
    </div>
  )
}
