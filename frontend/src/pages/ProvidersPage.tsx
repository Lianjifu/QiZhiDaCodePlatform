/**
 * 模型接入配置。
 *
 * **视觉能力那列是给对话用的** —— 能不能把图片内联进消息，取决于这条配置
 * 上的 ``vision_enabled``。
 */
import { useEffect, useState } from 'react'

import { listProviders } from '../api/resources'
import type { LLMProvider } from '../types/api'

export default function ProvidersPage() {
  const [providers, setProviders] = useState<LLMProvider[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    listProviders()
      .then((page) => {
        setProviders(page.items)
        setError('')
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
  }, [])

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-6">
      <h1 className="text-lg font-semibold tracking-tight text-ink">模型接入配置</h1>
      <p className="mt-1 text-sm text-ink-muted">
        对话用哪个模型、上下文窗口多大、能不能看图 —— 都在这儿配。
      </p>

      {error && (
        <div className="mt-4 rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </div>
      )}

      <div className="mt-4 overflow-hidden rounded-lg border border-line bg-raised">
        {providers.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-ink-faint">没有配置</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-sunken text-left text-xs text-ink-faint">
              <tr>
                <th className="px-4 py-2 font-medium">名称</th>
                <th className="px-4 py-2 font-medium">厂商</th>
                <th className="px-4 py-2 font-medium">模型</th>
                <th className="px-4 py-2 font-medium">窗口</th>
                <th className="px-4 py-2 font-medium">看图</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((provider) => (
                <tr key={provider.id} className="border-t border-line">
                  <td className="px-4 py-2">
                    {provider.name}
                    {provider.is_default && (
                      <span className="ml-2 rounded bg-accent-soft px-1.5 py-0.5 text-[10px] text-accent">
                        默认
                      </span>
                    )}
                    {!provider.is_active && (
                      <span className="ml-2 rounded bg-sunken px-1.5 py-0.5 text-[10px] text-ink-faint">
                        已停用
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{provider.provider}</td>
                  <td className="px-4 py-2 font-mono text-xs">{provider.default_model}</td>
                  <td className="px-4 py-2 text-xs text-ink-muted">
                    {provider.context_window ? provider.context_window.toLocaleString() : '未知'}
                  </td>
                  <td className="px-4 py-2 text-xs">
                    {provider.vision_enabled ? (
                      <span className="text-success">支持</span>
                    ) : (
                      <span className="text-ink-faint">不支持</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
