/**
 * MCP 服务器的新增 / 编辑表单。
 *
 * 三种连接方式字段不一样：**stdio** 要命令与参数，**sse / streamable_http**
 * 要地址与请求头。用下拉切换。
 */
import { useState } from 'react'

import { createMCPServer, updateMCPServer } from '../../api/resources'
import { JsonField, LinesField, Select, TagsField, TextArea, TextInput, Toggle } from '../../components/ui/Fields'
import Modal from '../../components/ui/Modal'
import Button from '../../components/ui/Button'
import type { MCPServer } from '../../types/api'

interface FormState {
  name: string
  description: string
  transport: 'stdio' | 'sse' | 'streamable_http'
  command: string
  args: string[]
  env: Record<string, string>
  url: string
  headers: Record<string, string>
  timeout: number
  tags: string[]
  enabled: boolean
}

function initialState(server: MCPServer | null): FormState {
  if (!server) {
    return {
      name: '',
      description: '',
      transport: 'stdio',
      command: '',
      args: [],
      env: {},
      url: '',
      headers: {},
      timeout: 30,
      tags: [],
      enabled: true,
    }
  }
  return {
    name: server.name,
    description: server.description ?? '',
    transport: server.transport,
    command: server.command ?? '',
    args: server.args,
    env: server.env,
    url: server.url ?? '',
    headers: server.headers,
    timeout: server.timeout,
    tags: server.tags,
    enabled: server.enabled,
  }
}

export default function MCPForm({
  server,
  onClose,
  onSaved,
}: {
  /** 传 null 是新增 */
  server: MCPServer | null
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<FormState>(() => initialState(server))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const isStdio = form.transport === 'stdio'

  const submit = async () => {
    if (!form.name.trim()) {
      setError('服务器名不能为空')
      return
    }
    if (isStdio && !form.command.trim()) {
      setError('stdio 方式要填命令')
      return
    }
    if (!isStdio && !form.url.trim()) {
      setError(`${form.transport} 方式要填地址`)
      return
    }
    setSaving(true)
    setError('')
    const payload = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      transport: form.transport,
      command: isStdio ? form.command.trim() : null,
      args: isStdio ? form.args.filter((item) => item.trim()) : [],
      env: isStdio ? form.env : {},
      url: isStdio ? null : form.url.trim(),
      headers: isStdio ? {} : form.headers,
      timeout: Number(form.timeout),
      tags: form.tags,
      enabled: form.enabled,
    }
    try {
      if (server) {
        await updateMCPServer(server.id, payload)
      } else {
        await createMCPServer(payload)
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
      title={server ? `编辑 MCP 服务器：${server.name}` : '新增 MCP 服务器'}
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
            label="服务器名"
            required
            value={form.name}
            onChange={(event) => set('name', event.target.value)}
            placeholder="如：filesystem"
          />
          <Select
            label="连接方式"
            required
            value={form.transport}
            onChange={(event) => set('transport', event.target.value as FormState['transport'])}
            options={[
              { value: 'stdio', label: 'stdio（本机命令）' },
              { value: 'sse', label: 'sse（SSE）' },
              { value: 'streamable_http', label: 'streamable http' },
            ]}
          />
        </div>

        <TextArea
          label="说明"
          rows={2}
          value={form.description}
          onChange={(event) => set('description', event.target.value)}
        />

        {isStdio ? (
          <>
            <TextInput
              label="命令"
              required
              value={form.command}
              onChange={(event) => set('command', event.target.value)}
              placeholder="如：npx"
            />
            <LinesField
              label="命令参数"
              value={form.args}
              onChange={(value) => set('args', value)}
              hint="一行一个，如 -y"
            />
            <JsonField
              label="环境变量"
              value={form.env}
              onChange={(value) => set('env', value as Record<string, string>)}
              hint='JSON 对象，如 {"API_KEY": "..."}'
            />
          </>
        ) : (
          <>
            <TextInput
              label="地址"
              required
              value={form.url}
              onChange={(event) => set('url', event.target.value)}
              placeholder="https://example.com/mcp"
            />
            <JsonField
              label="请求头"
              value={form.headers}
              onChange={(value) => set('headers', value as Record<string, string>)}
              hint='JSON 对象，如 {"Authorization": "Bearer ..."}'
            />
          </>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <TextInput
            label="超时（秒）"
            type="number"
            min="1"
            max="600"
            value={form.timeout}
            onChange={(event) => set('timeout', Number(event.target.value))}
          />
          <TagsField label="标签" value={form.tags} onChange={(value) => set('tags', value)} />
        </div>

        <Toggle label="启用" checked={form.enabled} onChange={(value) => set('enabled', value)} />
      </div>
    </Modal>
  )
}
