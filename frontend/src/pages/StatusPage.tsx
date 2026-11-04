/**
 * 系统状态页 —— **前后端联通的验证页**。
 *
 * 每一项都真的调一次后端接口，把**接口实际返回的内容**原样显示出来。
 * 后端没起、路径写错、响应结构变了，这一页立刻就能看出来。
 */
import { useEffect, useState } from 'react'

import { API_BASE, ApiError } from '../api/client'
import { health, listCapabilities, listProviders } from '../api/resources'
import Badge from '../components/ui/Badge'
import Button from '../components/ui/Button'
import PageHeader from '../components/ui/PageHeader'
import { formatJson } from '../lib/format'

type CheckState = 'pending' | 'ok' | 'error'

interface CheckResult {
  id: string
  name: string
  endpoint: string
  state: CheckState
  /** 接口实际返回的响应体 */
  payload?: unknown
  error?: { message: string; errorCode: string; status: number }
}

interface Check {
  id: string
  name: string
  endpoint: string
  run: () => Promise<unknown>
}

const CHECKS: Check[] = [
  {
    id: 'health',
    name: '健康检查',
    endpoint: 'GET /api/v1/health',
    run: () => health(),
  },
  {
    id: 'providers',
    name: '模型接入配置',
    endpoint: 'GET /api/v1/llm-providers',
    run: () => listProviders(),
  },
  {
    id: 'capabilities',
    name: '能力清单',
    endpoint: 'GET /api/v1/capabilities',
    run: () => listCapabilities(),
  },
]

export default function StatusPage() {
  const [results, setResults] = useState<CheckResult[]>(
    CHECKS.map((check) => ({
      id: check.id,
      name: check.name,
      endpoint: check.endpoint,
      state: 'pending',
    })),
  )
  // 首次进入就算"在跑"
  const [running, setRunning] = useState(true)
  // 改这个计数就重跑一遍 —— 避免在 effect 里同步 setState
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false

    // 每一项单独跑 —— 一项失败不该挡住别的项
    const run = async () => {
      const settled = await Promise.all(
        CHECKS.map(async (check) => {
          try {
            return { id: check.id, payload: await check.run() }
          } catch (error) {
            return { id: check.id, error }
          }
        }),
      )
      if (cancelled) return

      setResults(
        settled.map((item) => {
          const base = CHECKS.find((check) => check.id === item.id)!
          if ('payload' in item) {
            return { ...base, state: 'ok', payload: item.payload }
          }
          const apiError =
            item.error instanceof ApiError
              ? item.error
              : new ApiError(
                  0,
                  'UNKNOWN',
                  item.error instanceof Error ? item.error.message : String(item.error),
                )
          return {
            ...base,
            state: 'error',
            error: {
              message: apiError.message,
              errorCode: apiError.errorCode,
              status: apiError.status,
            },
          }
        }),
      )
      setRunning(false)
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [nonce])

  const okCount = results.filter((item) => item.state === 'ok').length

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-5 sm:px-6 sm:py-6">
      <PageHeader
        title="系统状态"
        description={
          <>
            每一项都是<strong className="font-medium text-ink">真实调用</strong>
            后端接口，下面显示的是接口实际返回的响应体。
          </>
        }
        actions={
          <Button
            variant="primary"
            size="lg"
            loading={running}
            onClick={() => {
              setRunning(true)
              setNonce((value) => value + 1)
            }}
          >
            重新检测
          </Button>
        }
      />

      <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-line bg-raised px-4 py-3 text-sm shadow-1">
        <span className="text-ink-muted">接口前缀</span>
        <code className="rounded bg-sunken px-1.5 py-0.5 font-mono text-xs">{API_BASE}</code>
        <span className="text-ink-muted">联通</span>
        {okCount === results.length ? (
          <Badge tone="success">
            {okCount} / {results.length} 项通过
          </Badge>
        ) : (
          <Badge tone="warning">
            {okCount} / {results.length} 项通过
          </Badge>
        )}
      </div>

      <div className="mt-4 space-y-3">
        {results.map((result) => (
          <section
            key={result.id}
            className="overflow-hidden rounded-xl border border-line bg-raised shadow-1"
          >
            <div className="flex items-center gap-3 border-b border-line px-4 py-3">
              <StatusDot state={result.state} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-ink">{result.name}</div>
                <code className="text-xs text-ink-faint">{result.endpoint}</code>
              </div>
            </div>
            <div className="px-4 py-3">
              {result.state === 'pending' ? (
                <p className="text-sm text-ink-faint">等待中…</p>
              ) : result.state === 'error' ? (
                <div className="text-sm">
                  <div className="text-danger">{result.error?.message}</div>
                  <div className="mt-1 text-xs text-ink-muted">
                    HTTP {result.error?.status} · {result.error?.errorCode}
                  </div>
                </div>
              ) : (
                <pre className="max-h-72 overflow-auto rounded-lg bg-sunken p-3 text-xs leading-relaxed text-ink-muted">
                  {formatJson(result.payload)}
                </pre>
              )}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}

function StatusDot({ state }: { state: CheckState }) {
  if (state === 'ok') {
    return <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-success" />
  }
  if (state === 'error') {
    return <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-danger" />
  }
  return <span className="h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-line-strong" />
}
