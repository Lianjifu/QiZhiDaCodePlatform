/**
 * 数据表 —— **宽屏是表格，窄屏是卡片**。
 *
 * 手机上表格没法看：横向滚动 + 列挤在一起，每一行都要横向拖。所以窄屏
 * 把每一行摊成一张卡片：主标题 + 副标题 + 几个小标签 + 操作。
 *
 * 同一份列定义，两种排法 —— 页面里不用写两遍。
 */
import type { ReactNode } from 'react'

/** 这一格在窄屏上放到哪儿。 */
export type MobileSlot = 'title' | 'subtitle' | 'meta' | 'hide'

export interface Column<T> {
  /** 表头文字 */
  header: string
  /** 这一格的内容 */
  render: (row: T) => ReactNode
  /** 窄屏上这一格的位置（不写就是只在表格里出现） */
  mobile?: MobileSlot
  /** 这一列宽一点（表格里的 class） */
  width?: string
  /** 表格里右对齐 */
  align?: 'left' | 'right'
}

export default function DataTable<T>({
  columns,
  rows,
  rowKey,
  actions,
  empty,
}: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string | number
  /** 行尾的操作（表格最后一格 / 卡片底部） */
  actions?: (row: T) => ReactNode
  /** 没有数据时显示什么 */
  empty?: ReactNode
}) {
  if (rows.length === 0) {
    return <>{empty ?? null}</>
  }

  const title = columns.find((column) => column.mobile === 'title')
  const subtitle = columns.find((column) => column.mobile === 'subtitle')
  const metas = columns.filter((column) => column.mobile === 'meta')

  return (
    <>
      {/* 宽屏：表格 */}
      <div className="hidden overflow-x-auto lg:block">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="bg-sunken text-left text-xs text-ink-faint">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.header}
                  className={[
                    'px-4 py-2.5 font-medium whitespace-nowrap',
                    column.align === 'right' ? 'text-right' : 'text-left',
                    column.width ?? '',
                  ].join(' ')}
                >
                  {column.header}
                </th>
              ))}
              {actions ? <th className="w-px px-4 py-2.5" /> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={rowKey(row)}
                className="border-t border-line transition-colors hover:bg-hover/40"
              >
                {columns.map((column) => (
                  <td
                    key={column.header}
                    className={[
                      'px-4 py-2.5 align-middle whitespace-nowrap',
                      column.align === 'right' ? 'text-right' : 'text-left',
                      column.width ?? '',
                    ].join(' ')}
                  >
                    {column.render(row)}
                  </td>
                ))}
                {actions ? (
                  <td className="px-4 py-2.5 text-right align-middle">
                    <div className="flex items-center justify-end gap-1">{actions(row)}</div>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 窄屏：卡片 */}
      <ul className="divide-y divide-line lg:hidden">
        {rows.map((row) => (
          <li key={rowKey(row)} className="px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                {title ? (
                  <div className="text-sm font-medium text-ink">{title.render(row)}</div>
                ) : null}
                {subtitle ? (
                  <div className="mt-0.5 text-xs text-ink-muted">{subtitle.render(row)}</div>
                ) : null}
              </div>
            </div>
            {metas.length > 0 ? (
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
                {metas.map((column) => (
                  <div key={column.header} className="text-xs text-ink-muted">
                    {column.render(row)}
                  </div>
                ))}
              </div>
            ) : null}
            {actions ? (
              <div className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-line pt-2.5">
                {actions(row)}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </>
  )
}
