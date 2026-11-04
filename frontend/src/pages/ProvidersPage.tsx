/**
 * 模型接入（供应商配置）。
 *
 * 一张表管所有配置：新增、编辑、删除、看详情、测连通性。
 *
 * **API Key 只有写没有读** —— 后端只回脱敏串（`sk-***1b5d`），编辑时留空
 * 就是"不修改"。
 *
 * 表格在宽屏上是表格，在窄屏上摊成卡片 —— 同一份列定义，页面里不用写两遍。
 */
import { useCallback, useEffect, useState } from 'react'

import {
  deleteProvider,
  listProviderPresets,
  listProviders,
  testProvider,
} from '../api/resources'
import Badge from '../components/ui/Badge'
import Banner from '../components/ui/Banner'
import Button from '../components/ui/Button'
import DataTable, { type Column } from '../components/ui/DataTable'
import EmptyState from '../components/ui/EmptyState'
import Modal from '../components/ui/Modal'
import PageHeader from '../components/ui/PageHeader'
import { useConfirm } from '../components/ui/useConfirm'
import ProviderForm from '../features/providers/ProviderForm'
import type { LLMProvider, ProviderPreset, ProviderTestResult } from '../types/api'

export default function ProvidersPage() {
  const { confirm, dialog: confirmDialog } = useConfirm()
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
    const ok = await confirm({
      title: `删掉「${provider.name}」？`,
      description: '用这条配置的会话会跑不起来 —— 它们会退回默认模型，或者直接报错。',
      confirmText: '删掉',
      danger: true,
    })
    if (!ok) return
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

  const columns: Column<LLMProvider>[] = [
    {
      header: '名称',
      mobile: 'title',
      render: (provider) => (
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-ink">{provider.name}</span>
          {provider.is_default ? <Badge tone="accent">默认</Badge> : null}
          {!provider.is_active ? <Badge>已停用</Badge> : null}
        </span>
      ),
    },
    {
      header: '模型',
      mobile: 'subtitle',
      render: (provider) => (
        <span className="font-mono text-xs text-ink-muted">{provider.default_model}</span>
      ),
    },
    {
      header: '厂商',
      mobile: 'meta',
      render: (provider) => <span className="text-xs text-ink-muted">{provider.provider}</span>,
    },
    {
      header: '上下文窗口',
      mobile: 'meta',
      render: (provider) => (
        <span className="text-xs tabular-nums text-ink-muted">
          {provider.context_window ? provider.context_window.toLocaleString() : '预设'}
        </span>
      ),
    },
    {
      header: '状态',
      render: (provider) =>
        provider.is_active ? <Badge tone="success">启用</Badge> : <Badge>停用</Badge>,
    },
  ]

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6 sm:py-6">
      <PageHeader
        title="模型接入"
        description="每个供应商一条配置 —— 接口地址、密钥、模型、上下文窗口。默认那条是新会话用的。"
        actions={
          <Button variant="primary" size="lg" onClick={() => setEditing(null)}>
            新增配置
          </Button>
        }
      />

      {error ? (
        <Banner tone="danger" className="mt-4">
          {error}
        </Banner>
      ) : null}

      <div className="mt-5 overflow-hidden rounded-xl border border-line bg-raised shadow-1">
        <DataTable
          columns={columns}
          rows={providers}
          rowKey={(provider) => provider.id}
          actions={(provider) => (
            <>
              <Button size="sm" variant="ghost" onClick={() => setViewing(provider)}>
                详情
              </Button>
              <Button
                size="sm"
                variant="ghost"
                loading={testingId === provider.id}
                onClick={() => void runTest(provider)}
              >
                测试
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(provider)}>
                编辑
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void remove(provider)}>
                <span className="text-danger">删除</span>
              </Button>
            </>
          )}
          empty={
            <EmptyState
              title="还没有配置"
              description="加一条供应商配置，新会话就能用它跑对话了。"
              action={
                <Button variant="primary" onClick={() => setEditing(null)}>
                  新增配置
                </Button>
              }
            />
          }
        />
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
        <Modal
          title={`详情：${viewing.name}`}
          onClose={() => setViewing(null)}
          size="lg"
          description={viewing.description || undefined}
        >
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <DetailRow label="厂商" value={viewing.provider} />
            <DetailRow label="默认模型" value={viewing.default_model} mono />
            <DetailRow label="接口地址" value={viewing.base_url || '（预设默认）'} mono />
            <DetailRow
              label="上下文窗口"
              value={viewing.context_window ? viewing.context_window.toLocaleString() : '（预设）'}
            />
            <DetailRow
              label="可选模型"
              value={viewing.available_models.join('、') || '（未填）'}
              mono
            />
            <DetailRow label="API Key" value={viewing.api_key_masked || '（未配置）'} mono />
            <DetailRow label="温度" value={String(viewing.temperature)} />
            <DetailRow label="最大输出 token" value={String(viewing.max_tokens)} />
            <DetailRow label="超时" value={`${viewing.timeout} 秒`} />
            <DetailRow label="看图" value={viewing.vision_enabled ? '支持' : '不支持'} />
            <DetailRow label="状态" value={viewing.is_active ? '启用' : '停用'} />
            <DetailRow label="默认" value={viewing.is_default ? '是' : '否'} />
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
          <Banner tone={testing.ok ? 'success' : 'danger'}>
            {testing.ok ? '连通' : '不通'}
            {testing.latency_ms ? ` · ${testing.latency_ms} ms` : ''}
          </Banner>
          <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-sunken p-3 text-xs leading-relaxed text-ink-muted">
            {testing.message}
          </pre>
        </Modal>
      ) : null}

      {confirmDialog}
    </div>
  )
}

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-faint">{label}</dt>
      <dd
        className={[
          'mt-0.5 text-sm break-words',
          mono ? 'font-mono text-xs text-ink' : 'text-ink',
        ].join(' ')}
      >
        {value}
      </dd>
    </div>
  )
}
