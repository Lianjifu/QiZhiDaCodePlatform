/**
 * 工具（原子能力）的新增 / 编辑表单。
 *
 * 三种类型字段不一样，用一个类型下拉切换：**builtin** 要来源（内置白名单里的
 * 入口点）、**http** 要接口地址与入参 schema、**mcp** 要服务器名与工具名。
 */
import { useState } from 'react'

import { createTool, updateTool } from '../../api/resources'
import { JsonField, Select, TagsField, TextArea, TextInput, Toggle } from '../../components/ui/Fields'
import Modal from '../../components/ui/Modal'
import Button from '../../components/ui/Button'
import type { BuiltinTool, MCPServer, Tool } from '../../types/api'

interface FormState {
  name: string
  display_name: string
  description: string
  type: 'builtin' | 'http' | 'mcp'
  source: string
  config: Record<string, unknown>
  parameters: Record<string, unknown>
  tags: string[]
  permission: string
  risk: string
  timeout_s: number
  enabled: boolean
}

function initialState(tool: Tool | null): FormState {
  if (!tool) {
    return {
      name: '',
      display_name: '',
      description: '',
      type: 'builtin',
      source: '',
      config: {},
      parameters: {},
      tags: [],
      permission: 'none',
      risk: 'read',
      timeout_s: 30,
      enabled: true,
    }
  }
  return {
    name: tool.name,
    display_name: tool.display_name,
    description: tool.description,
    type: tool.type,
    source: tool.source ?? '',
    config: tool.config,
    parameters: tool.parameters,
    tags: tool.tags,
    permission: tool.permission,
    risk: tool.risk,
    timeout_s: tool.timeout_s,
    enabled: tool.enabled,
  }
}

export default function ToolForm({
  tool,
  builtins,
  servers,
  onClose,
  onSaved,
}: {
  /** 传 null 是新增 */
  tool: Tool | null
  /** 内置能力白名单 —— builtin 类型从这里选 */
  builtins: BuiltinTool[]
  /** 已配置的 MCP 服务器 —— mcp 类型从这里选 */
  servers: MCPServer[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<FormState>(() => initialState(tool))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const submit = async () => {
    if (!form.name.trim()) {
      setError('工具名不能为空')
      return
    }
    setSaving(true)
    setError('')
    const payload = {
      name: form.name.trim(),
      display_name: form.display_name.trim(),
      description: form.description.trim(),
      type: form.type,
      source: form.source.trim() || null,
      config: form.config,
      parameters: form.parameters,
      tags: form.tags,
      permission: form.permission,
      risk: form.risk,
      timeout_s: Number(form.timeout_s),
      enabled: form.enabled,
    }
    try {
      if (tool) {
        await updateTool(tool.id, payload)
      } else {
        await createTool(payload)
      }
      onSaved()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={tool ? `编辑工具：${tool.name}` : '新增工具'}
      onClose={onClose}
      wide
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button variant="primary" onClick={() => void submit()} disabled={saving}>
            {saving ? '保存中…' : '保存'}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {error ? (
          <div className="rounded-lg border border-danger bg-danger-soft px-3 py-2 text-xs text-danger">
            {error}
          </div>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput
            label="工具名"
            required
            value={form.name}
            onChange={(event) => set('name', event.target.value)}
            placeholder="字母开头，如 web_fetch"
            hint="会直接进 LLM 的工具 schema"
          />
          <TextInput
            label="显示名"
            value={form.display_name}
            onChange={(event) => set('display_name', event.target.value)}
            placeholder="给人看的名字"
          />
        </div>

        <TextArea
          label="说明"
          rows={2}
          value={form.description}
          onChange={(event) => set('description', event.target.value)}
          hint="给模型看的 —— 写清楚这个工具什么时候该用"
        />

        <div className="grid gap-3 sm:grid-cols-2">
          <Select
            label="类型"
            required
            value={form.type}
            onChange={(event) => set('type', event.target.value as FormState['type'])}
            options={[
              { value: 'builtin', label: 'builtin（平台内置）' },
              { value: 'http', label: 'http（HTTP 接口）' },
              { value: 'mcp', label: 'mcp（MCP 服务器）' },
            ]}
          />
          {form.type === 'builtin' ? (
            <Select
              label="内置来源"
              required
              value={form.source}
              onChange={(event) => set('source', event.target.value)}
              options={[
                { value: '', label: '选一个内置能力' },
                ...builtins.map((item) => ({
                  value: item.source,
                  label: `${item.display_name}（${item.key}）`,
                })),
              ]}
              hint="只能从白名单里选 —— 平台不允许自建任意入口"
            />
          ) : null}
          {form.type === 'mcp' ? (
            <Select
              label="MCP 服务器"
              required
              value={form.source}
              onChange={(event) => set('source', event.target.value)}
              options={[
                { value: '', label: '选一个 MCP 服务器' },
                ...servers.map((item) => ({ value: item.name, label: item.name })),
              ]}
              hint="先到「MCP」标签页加一个服务器"
            />
          ) : null}
        </div>

        {form.type === 'http' ? (
          <TextInput
            label="接口地址"
            required
            value={String(form.config.url ?? '')}
            onChange={(event) => set('config', { ...form.config, url: event.target.value })}
            placeholder="https://example.com/api"
          />
        ) : null}

        {form.type === 'mcp' ? (
          <TextInput
            label="MCP 工具名"
            required
            value={String(form.config.tool_name ?? '')}
            onChange={(event) => set('config', { ...form.config, tool_name: event.target.value })}
            placeholder="服务器上那个工具叫什么"
          />
        ) : null}

        <JsonField
          label={form.type === 'http' ? '其他配置' : '配置'}
          value={form.config}
          onChange={(value) => set('config', value)}
          hint="JSON 对象，如 http 类型的 method / headers"
        />

        {form.type === 'http' ? (
          <JsonField
            label="入参 schema"
            value={form.parameters}
            onChange={(value) => set('parameters', value)}
            hint="JSON Schema，必须有 type 或 properties"
          />
        ) : null}

        <div className="grid gap-3 sm:grid-cols-3">
          <Select
            label="权限要求"
            value={form.permission}
            onChange={(event) => set('permission', event.target.value)}
            options={[
              { value: 'none', label: '无需确认' },
              { value: 'confirm', label: '要人确认' },
              { value: 'admin', label: '管理员放行' },
            ]}
          />
          <Select
            label="风险等级"
            value={form.risk}
            onChange={(event) => set('risk', event.target.value)}
            options={[
              { value: 'read', label: '只读' },
              { value: 'write', label: '写入' },
              { value: 'execute', label: '执行' },
              { value: 'network', label: '联网' },
              { value: 'destructive', label: '破坏性' },
            ]}
          />
          <TextInput
            label="超时（秒）"
            type="number"
            min="1"
            max="600"
            value={form.timeout_s}
            onChange={(event) => set('timeout_s', Number(event.target.value))}
          />
        </div>

        <TagsField label="标签" value={form.tags} onChange={(value) => set('tags', value)} />

        <Toggle label="启用" checked={form.enabled} onChange={(value) => set('enabled', value)} />
      </div>
    </Modal>
  )
}
