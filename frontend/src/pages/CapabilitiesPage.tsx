/**
 * 统一能力清单。
 *
 * 后端把工具、技能、MCP 都归到一张清单上 —— agent 只从这儿取能力。
 */
import { useEffect, useState } from 'react'

import { listCapabilities } from '../api/resources'
import type { Capability } from '../types/api'

export default function CapabilitiesPage() {
  const [items, setItems] = useState<Capability[]>([])
  const [kinds, setKinds] = useState<string[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    listCapabilities()
      .then((result) => {
        setItems(result.items)
        setKinds(result.kinds)
        setError('')
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
  }, [])

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-6">
      <h1 className="text-lg font-semibold tracking-tight text-ink">能力清单</h1>
      <p className="mt-1 text-sm text-ink-muted">
        工具、技能、MCP 都在这一张清单里 —— agent 只从注册层取能力，不认别的来源。
        {kinds.length > 0 && `（${kinds.join(' / ')}）`}
      </p>

      {error && (
        <div className="mt-4 rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </div>
      )}

      <div className="mt-4 space-y-2">
        {items.length === 0 ? (
          <div className="rounded-lg border border-line bg-raised px-4 py-10 text-center text-sm text-ink-faint">
            清单是空的
          </div>
        ) : (
          items.map((item) => (
            <div key={`${item.kind}-${item.key}`} className="rounded-lg border border-line bg-raised px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="rounded bg-sunken px-1.5 py-0.5 text-[10px] text-ink-faint">
                  {item.kind}
                </span>
                <span className="font-mono text-sm text-ink">{item.key}</span>
                {item.display_name && (
                  <span className="text-sm text-ink-muted">{item.display_name}</span>
                )}
                {item.enabled === false && (
                  <span className="rounded bg-sunken px-1.5 py-0.5 text-[10px] text-ink-faint">
                    已停用
                  </span>
                )}
              </div>
              {item.description && (
                <div className="mt-1 text-xs text-ink-muted">{item.description}</div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
