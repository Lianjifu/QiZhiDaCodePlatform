/**
 * 供应商配置的新增 / 编辑表单。
 *
 * 一套表单管两种模式：**新增**（空表单）与**编辑**（带上已有值）。
 * 模式靠 `provider` 有没有值来分 —— 编辑时 API Key 留空就是"不改"。
 */
import { useState } from 'react'

import { createProvider, updateProvider } from '../../api/resources'
import { Select, TagsField, TextArea, TextInput, Toggle } from '../../components/ui/Fields'
import Banner from '../../components/ui/Banner'
import FormSection from '../../components/ui/FormSection'
import Modal from '../../components/ui/Modal'
import Button from '../../components/ui/Button'
import type { LLMProvider, ProviderPreset } from '../../types/api'

/** 表单里的东西。 */
interface FormState {
  name: string
  provider: string
  base_url: string
  api_key: string
  default_model: string
  available_models: string[]
  embedding_model: string
  temperature: number
  max_tokens: number
  context_window: number
  /** 跟随预设 / 支持 / 不支持 */
  vision: 'preset' | 'yes' | 'no'
  timeout: number
  is_default: boolean
  is_active: boolean
  description: string
}

function initialState(provider: LLMProvider | null): FormState {
  if (!provider) {
    return {
      name: '',
      provider: 'openai',
      base_url: '',
      api_key: '',
      default_model: '',
      available_models: [],
      embedding_model: '',
      temperature: 0,
      max_tokens: 4096,
      context_window: 0,
      vision: 'preset',
      timeout: 60,
      is_default: false,
      is_active: true,
      description: '',
    }
  }
  return {
    name: provider.name,
    provider: provider.provider,
    base_url: provider.base_url ?? '',
    api_key: '',
    default_model: provider.default_model,
    available_models: provider.available_models,
    embedding_model: provider.embedding_model,
    temperature: provider.temperature,
    max_tokens: provider.max_tokens,
    context_window: provider.context_window,
    vision: 'yes',
    timeout: provider.timeout,
    is_default: provider.is_default,
    is_active: provider.is_active,
    description: provider.description ?? '',
  }
}

export default function ProviderForm({
  provider,
  presets,
  onClose,
  onSaved,
}: {
  /** 传 null 是新增 */
  provider: LLMProvider | null
  presets: ProviderPreset[]
  onClose: () => void
  onSaved: (provider: LLMProvider) => void
}) {
  const [form, setForm] = useState<FormState>(() => initialState(provider))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const preset = presets.find((item) => item.key === form.provider)

  const submit = async () => {
    if (!form.name.trim()) {
      setError('配置名称不能为空')
      return
    }
    if (!form.default_model.trim()) {
      setError('默认模型不能为空')
      return
    }
    setSaving(true)
    setError('')
    const payload: Record<string, unknown> = {
      name: form.name.trim(),
      provider: form.provider,
      base_url: form.base_url.trim() || null,
      default_model: form.default_model.trim(),
      available_models: form.available_models,
      embedding_model: form.embedding_model.trim(),
      temperature: Number(form.temperature),
      max_tokens: Number(form.max_tokens),
      context_window: Number(form.context_window),
      vision_enabled: form.vision === 'preset' ? null : form.vision === 'yes',
      timeout: Number(form.timeout),
      is_default: form.is_default,
      is_active: form.is_active,
      description: form.description.trim() || null,
    }
    // 编辑时留空 = 不改 key；新增时空着就报错（后端会拦）
    if (form.api_key.trim()) payload.api_key = form.api_key.trim()

    try {
      const saved = provider
        ? await updateProvider(provider.id, payload)
        : await createProvider(payload)
      onSaved(saved)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={provider ? `编辑：${provider.name}` : '新增供应商配置'}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button variant="primary" loading={saving} onClick={() => void submit()}>
            保存
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {error ? <Banner tone="danger">{error}</Banner> : null}

        <FormSection title="接入">
        <div className="grid gap-3.5 sm:grid-cols-2">
          <TextInput
            label="配置名称"
            required
            value={form.name}
            onChange={(event) => set('name', event.target.value)}
            placeholder="如：DeepSeek 生产"
          />
          <Select
            label="厂商"
            required
            value={form.provider}
            onChange={(event) => set('provider', event.target.value)}
            options={presets.map((item) => ({ value: item.key, label: item.display_name }))}
            hint={preset ? `API Key 环境变量：${preset.api_key_env}` : undefined}
          />
        </div>

        <TextInput
          label="接口地址"
          value={form.base_url}
          onChange={(event) => set('base_url', event.target.value)}
          placeholder={preset?.default_base_url || '留空用预设默认地址'}
        />

        <TextInput
          label="API Key"
          type="password"
          value={form.api_key}
          onChange={(event) => set('api_key', event.target.value)}
          placeholder={provider?.has_api_key ? '留空表示不修改' : '必填'}
          hint={
            provider?.has_api_key ? `当前：${provider.api_key_masked}` : '只加密存服务端，明文不会回显'
          }
        />

        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput
            label="默认模型"
            required
            value={form.default_model}
            onChange={(event) => set('default_model', event.target.value)}
            placeholder="如：deepseek-flash"
          />
          <TagsField
            label="可选模型"
            value={form.available_models}
            onChange={(value) => set('available_models', value)}
          />
        </div>

        <div className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
          <TextInput
            label="温度"
            type="number"
            step="0.1"
            min="0"
            max="2"
            value={form.temperature}
            onChange={(event) => set('temperature', Number(event.target.value))}
          />
          <TextInput
            label="最大输出 token"
            type="number"
            min="1"
            value={form.max_tokens}
            onChange={(event) => set('max_tokens', Number(event.target.value))}
          />
          <TextInput
            label="上下文窗口"
            type="number"
            min="0"
            value={form.context_window}
            onChange={(event) => set('context_window', Number(event.target.value))}
            hint="0 = 按厂商预设"
          />
          <TextInput
            label="超时（秒）"
            type="number"
            min="1"
            value={form.timeout}
            onChange={(event) => set('timeout', Number(event.target.value))}
          />
        </div>

        </FormSection>

        <FormSection title="其他">
        <div className="grid gap-3.5 sm:grid-cols-2">
          <TextInput
            label="向量化模型"
            value={form.embedding_model}
            onChange={(event) => set('embedding_model', event.target.value)}
            hint="留空表示这条配置不参与向量化"
          />
          <Select
            label="看图"
            value={form.vision}
            onChange={(event) => set('vision', event.target.value as FormState['vision'])}
            options={[
              { value: 'preset', label: '跟随厂商预设' },
              { value: 'yes', label: '支持看图' },
              { value: 'no', label: '不支持' },
            ]}
          />
        </div>

        <TextArea
          label="描述"
          rows={2}
          value={form.description}
          onChange={(event) => set('description', event.target.value)}
        />

        <div className="flex flex-wrap gap-x-8 gap-y-3 rounded-xl border border-line bg-sunken px-4 py-3">
          <Toggle
            label="启用"
            checked={form.is_active}
            onChange={(value) => set('is_active', value)}
          />
          <Toggle
            label="设为默认"
            checked={form.is_default}
            onChange={(value) => set('is_default', value)}
            hint="默认供应商全局唯一"
          />
        </div>
        </FormSection>
      </div>
    </Modal>
  )
}
