/**
 * 模型接入（供应商配置）。
 *
 * 一张表管所有配置：新增、编辑、删除、看详情、测连通性。
 *
 * **API Key 只有写没有读** —— 后端只回脱敏串（`sk-***1b5d`），编辑时留空
 * 就是"不修改"。
 */
import { useCallback, useEffect, useState } from 'react'

import {
  deleteProvider,
  listProviderPresets,
  listProviders,
  testProvider,
} from '../api/resources'
import Button from '../components/ui/Button'
import Modal from '../components/ui/Modal'
import ProviderForm from '../features/providers/ProviderForm'
import type { LLMProvider, ProviderPreset, ProviderTestResult } from '../types/api'

export default function ProvidersPage() {
  const [providers, setProviders] = useState<LLMProvider[]>([])
  const [presets, setPresets] = useState<ProviderPreset[]>([])
  const [error, setError] = useState('')
  /** 正在编辑的那条（null = 新增，undefined = 不开表单） */
  const [editing, setEditing] = useState<LLMProvider | null | undefined>(undefined)
  const [viewing, setViewing] = useState<LLMProvider | null>(null)
  const [testing, setTesting] = useState<ProviderTestResult | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)

  /** 读数据。分开写是为了让 effect 里的 setState 落在 then 里（同步调用会被 lint 判成级联渲染）。 */
  const load = useCallback(async () => {
    const [page, presetList] = await Promise.all([listProviders(), listProviderPresets()])
    return { page, presetList }
  }, [])

  const apply = (data: { page: Awaited<ReturnType<typeof listProviders>>; presetList: ProviderPreset[] }) => {
    setProviders(data.page.items)
    setPresets(data.presetList)
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

  const remove = async (provider: LLMProvider) => {
    if (!window.confirm(`删掉「${provider.name}」？用这条配置的会话会跑不起来。`)) return
    try {
      await deleteProvider(provider.id)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const runTest = async (provider: LLMProvider) => {
    setTestingId(provider.id)
    try {
      setTesting(await testProvider(provider.id))
    } catch (cause) {
      setTesting({
        ok: false,
        provider_id: provider.id,
        model: provider.default_model,
        latency_ms: 0,
        message: cause instanceof Error ? cause.message : String(cause),
      })
    } finally {
      setTestingId(null)
    }
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">模型接入</h1>
          <p className="mt-1 text-sm text-ink-muted">
            每个供应商一条配置 —— 接口地址、密钥、模型、上下文窗口。默认那条是
            新会话用的。
          </p>
        </div>
        <Button variant="primary" onClick={() => setEditing(null)}>
          新增配置
        </Button>
      </div>

      {error ? (
        <div className="mt-4 rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </div>
      ) : null}

      <div className="mt-4 overflow-hidden rounded-lg border border-line bg-raised">
        {providers.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-ink-faint">还没有配置</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-sunken text-left text-xs text-ink-faint">
              <tr>
                <th className="px-4 py-2 font-medium">名称</th>
                <th className="px-4 py-2 font-medium">厂商</th>
                <th className="px-4 py-2 font-medium">模型</th>
                <th className="px-4 py-2 font-medium">窗口</th>
                <th className="px-4 py-2 font-medium">状态</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {providers.map((provider) => (
                <tr key={provider.id} className="border-t border-line">
                  <td className="px-4 py-2">
                    <span className="text-ink">{provider.name}</span>
                    {provider.is_default && (
                      <span className="ml-2 rounded bg-accent-soft px-1.5 py-0.5 text-[10px] text-accent">
                        默认
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{provider.provider}</td>
                  <td className="px-4 py-2 font-mono text-xs">{provider.default_model}</td>
                  <td className="px-4 py-2 text-xs text-ink-muted">
                    {provider.context_window ? provider.context_window.toLocaleString() : '预设'}
                  </td>
                  <td className="px-4 py-2 text-xs">
                    {provider.is_active ? (
                      <span className="text-success">启用</span>
                    ) : (
                      <span className="text-ink-faint">停用</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => setViewing(provider)}
                        className="text-xs text-ink-faint hover:text-ink"
                      >
                        详情
                      </button>
                      <button
                        type="button"
                        onClick={() => void runTest(provider)}
                        disabled={testingId === provider.id}
                        className="text-xs text-ink-faint hover:text-accent disabled:opacity-50"
                      >
                        {testingId === provider.id ? '测试中…' : '测试'}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditing(provider)}
                        className="text-xs text-accent hover:underline"
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        onClick={() => void remove(provider)}
                        className="text-xs text-ink-faint hover:text-danger"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {editing !== undefined ? (
        <ProviderForm
          provider={editing}
          presets={presets}
          onClose={() => setEditing(undefined)}
          onSaved={() => {
            setEditing(undefined)
            void refresh()
          }}
        />
      ) : null}

      {viewing ? (
        <Modal title={`详情：${viewing.name}`} onClose={() => setViewing(null)} wide>
          <dl className="space-y-2 text-sm">
            <DetailRow label="厂商" value={viewing.provider} />
            <DetailRow label="接口地址" value={viewing.base_url || '（预设默认）'} mono />
            <DetailRow label="默认模型" value={viewing.default_model} mono />
            <DetailRow
              label="可选模型"
              value={viewing.available_models.join('、') || '（未填）'}
              mono
            />
            <DetailRow label="API Key" value={viewing.api_key_masked || '（未配置）'} mono />
            <DetailRow
              label="上下文窗口"
              value={viewing.context_window ? viewing.context_window.toLocaleString() : '（预设）'}
            />
            <DetailRow label="温度" value={String(viewing.temperature)} />
            <DetailRow label="最大输出 token" value={String(viewing.max_tokens)} />
            <DetailRow label="超时" value={`${viewing.timeout} 秒`} />
            <DetailRow label="看图" value={viewing.vision_enabled ? '支持' : '不支持'} />
            <DetailRow label="状态" value={viewing.is_active ? '启用' : '停用'} />
            <DetailRow label="默认" value={viewing.is_default ? '是' : '否'} />
            <DetailRow label="描述" value={viewing.description || '（无）'} />
          </dl>
        </Modal>
      ) : null}

      {testing ? (
        <Modal
          title="连通性测试"
          onClose={() => setTesting(null)}
          footer={
            <Button variant="ghost" onClick={() => setTesting(null)}>
              知道了
            </Button>
          }
        >
          <div
            className={[
              'rounded-lg border px-3 py-2 text-sm',
              testing.ok
                ? 'border-success bg-success-soft text-success'
                : 'border-danger bg-danger-soft text-danger',
            ].join(' ')}
          >
            {testing.ok ? '连通' : '不通'}
            {testing.latency_ms ? ` · ${testing.latency_ms} ms` : ''}
          </div>
          <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-sunken p-2 text-xs text-ink-muted">
            {testing.message}
          </pre>
        </Modal>
      ) : null}
    </div>
  )
}

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-3">
      <dt className="w-24 shrink-0 text-xs text-ink-faint">{label}</dt>
      <dd className={mono ? 'min-w-0 flex-1 font-mono text-xs text-ink' : 'min-w-0 flex-1 text-ink'}>
        {value}
      </dd>
    </div>
  )
}
