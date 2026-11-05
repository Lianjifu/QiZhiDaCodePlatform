/**
 * 进化记录：agent 自己改了什么。
 *
 * 复盘是**免审批**的 —— 平台不让人先看一眼再改，那这一页就是"事后查得到、
 * 撤得回"的地方：
 *
 * | 看到什么 | 说明什么 |
 * | --- | --- |
 * | 状态 | 改进落地了、被守卫拦了、还是撤销了 |
 * | 载体 | 改进落进了记忆、技能还是提示词 |
 * | 复发率 | 同类失败在改进之后还发不发生 —— **改进有没有用的唯一依据** |
 *
 * 能撤销：记忆删掉、技能追加的那段删回去、提示词规则停用。
 */
import { useCallback, useEffect, useState } from 'react'

import {
  getRetrospectiveMetrics,
  listRetrospectives,
  revertRetrospective,
} from '../api/resources'
import Badge from '../components/ui/Badge'
import Banner from '../components/ui/Banner'
import Button from '../components/ui/Button'
import DataTable, { type Column } from '../components/ui/DataTable'
import EmptyState from '../components/ui/EmptyState'
import Modal from '../components/ui/Modal'
import PageHeader from '../components/ui/PageHeader'
import { useConfirm } from '../components/ui/useConfirm'
import {
  carrierLabel,
  recurrenceText,
  rejectReasonLabel,
  statusLabel,
  statusTone,
} from '../features/autonomy/labels'
import type { Retrospective, RetrospectiveMetrics } from '../types/api'

export default function RetrospectivesPage() {
  const { confirm, dialog: confirmDialog } = useConfirm()
  const [rows, setRows] = useState<Retrospective[]>([])
  const [metrics, setMetrics] = useState<RetrospectiveMetrics | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [viewing, setViewing] = useState<Retrospective | null>(null)

  /** 读数据。分开写是为了让 effect 里的 setState 落在 then 里（同步调用会被 lint 判成级联渲染）。 */
  const load = useCallback(async () => {
    const [page, metric] = await Promise.all([
      listRetrospectives({ page_size: 100 }),
      getRetrospectiveMetrics(),
    ])
    return { page, metric }
  }, [])

  const apply = (data: {
    page: Awaited<ReturnType<typeof listRetrospectives>>
    metric: Awaited<ReturnType<typeof getRetrospectiveMetrics>>
  }) => {
    setRows(data.page.items)
    setMetrics(data.metric)
    setError('')
  }

  const refresh = useCallback(async () => {
    try {
      apply(await load())
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [load])

  useEffect(() => {
    let cancelled = false
    void load()
      .then((data) => {
        if (!cancelled) apply(data)
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      })
    return () => {
      cancelled = true
    }
  }, [load])

  const run = async (action: () => Promise<unknown>, message: string) => {
    try {
      await action()
      setNote(message)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  /** 复发率按复盘 id 找 —— 度量与记录是同一批。 */
  const recurrenceOf = (id: number) =>
    metrics?.recurrence.find((item) => item.retrospective_id === id)

  const columns: Column<Retrospective>[] = [
    {
      header: '改进',
      mobile: 'title',
      render: (row) => (
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={statusTone(row.status)}>{statusLabel(row.status)}</Badge>
            <Badge>{carrierLabel(row.carrier)}</Badge>
            <span className="text-sm font-medium text-ink">{row.title || '（无标题）'}</span>
          </div>
          {row.content ? (
            <div className="mt-1.5 text-xs leading-relaxed text-ink-muted">{row.content}</div>
          ) : null}
          {row.reject_reason ? (
            <div className="mt-1 text-xs text-warning">
              被拒原因：{rejectReasonLabel(row.reject_reason)}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      header: '来源',
      render: (row) => (
        <span className="text-xs text-ink-muted">
          {row.thread_id || '—'}
          {row.invocation_count > 0 ? ` · ${row.invocation_count} 次失败` : ''}
        </span>
      ),
    },
    {
      header: '复发率',
      render: (row) => {
        const recurrence = recurrenceOf(row.id)
        if (!recurrence) {
          return <span className="text-xs text-ink-muted">—</span>
        }
        const text = recurrenceText(
          recurrence.failures_before,
          recurrence.failures_after,
          recurrence.recurrence_rate,
        )
        return <span className="text-xs text-ink-muted">{text ?? '—'}</span>
      },
    },
  ]

  const total = metrics?.total ?? 0
  const applied = metrics?.by_status?.applied ?? 0
  const blocked = metrics?.by_reject_reason?.safety_scope ?? 0

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6 sm:py-6">
      <PageHeader
        title="进化记录"
        description="agent 每轮失败后会自己复盘一次，把学到的东西落进记忆、技能或提示词 —— 这里记着它改了什么，也能撤回去。"
        meta={total > 0 ? `${total} 次复盘 · ${applied} 条已生效` : undefined}
      />

      {error ? (
        <Banner tone="danger" className="mt-4">
          {error}
        </Banner>
      ) : null}
      {note ? (
        <Banner tone="success" className="mt-4">
          {note}
        </Banner>
      ) : null}
      {blocked > 0 ? (
        <Banner tone="warning" className="mt-4">
          有 {blocked} 条改进被范围守卫拦下了 —— 那些想改权限与审批的建议一律不应用。
        </Banner>
      ) : null}

      <div className="mt-4 overflow-hidden rounded-xl border border-line bg-raised shadow-1">
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(row) => row.id}
          actions={(row) => (
            <>
              <Button size="sm" variant="ghost" onClick={() => setViewing(row)}>
                详情
              </Button>
              {row.status === 'applied' ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    void (async () => {
                      const ok = await confirm({
                        title: `撤销「${row.title || '这条改进'}」？`,
                        description:
                          carrierLabel(row.carrier) === '提示词'
                            ? '这条规则会停用，不再进 agent 的提示词。'
                            : carrierLabel(row.carrier) === '技能'
                              ? '技能里追加的那段会被删回去。'
                              : '那条记忆会被删掉。',
                        confirmText: '撤销',
                        danger: true,
                      })
                      if (!ok) return
                      await run(async () => {
                        const result = await revertRetrospective(row.id)
                        if (!result.reverted) throw new Error(result.message || '撤销失败')
                      }, '已撤销')
                    })()
                  }}
                >
                  <span className="text-danger">撤销</span>
                </Button>
              ) : null}
            </>
          )}
          empty={
            <EmptyState
              title="还没有复盘记录"
              description="agent 在一轮里失败过之后才会复盘 —— 没有失败就没有记录。"
            />
          }
        />
      </div>

      {viewing ? (
        <Modal title="复盘详情" onClose={() => setViewing(null)} size="lg">
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-lg bg-sunken p-3 font-mono text-xs leading-relaxed text-ink-muted">
            {JSON.stringify(viewing, null, 2)}
          </pre>
        </Modal>
      ) : null}

      {confirmDialog}
    </div>
  )
}
