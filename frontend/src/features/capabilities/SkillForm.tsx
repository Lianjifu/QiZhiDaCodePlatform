/**
 * 技能的新增 / 编辑表单。
 *
 * 技能是**能力的组合** —— 一段"核心做法"加上它能用的工具。挂载时核心做法会
 * 追加到系统提示词里。
 */
import { useState } from 'react'

import { createSkill, updateSkill } from '../../api/resources'
import { LinesField, TagsField, TextArea, TextInput, Toggle } from '../../components/ui/Fields'
import Banner from '../../components/ui/Banner'
import { Checkbox } from '../../components/ui/Fields'
import FormSection from '../../components/ui/FormSection'
import Modal from '../../components/ui/Modal'
import Button from '../../components/ui/Button'
import type { Skill, Tool } from '../../types/api'

interface FormState {
  name: string
  description: string
  knowledge: string
  checklist: string[]
  examples: string[]
  constraints: string
  tags: string[]
  enabled: boolean
  tool_ids: number[]
}

function initialState(skill: Skill | null): FormState {
  if (!skill) {
    return {
      name: '',
      description: '',
      knowledge: '',
      checklist: [],
      examples: [],
      constraints: '',
      tags: [],
      enabled: true,
      tool_ids: [],
    }
  }
  return {
    name: skill.name,
    description: skill.description ?? '',
    knowledge: skill.knowledge,
    checklist: skill.checklist,
    examples: skill.examples,
    constraints: skill.constraints,
    tags: skill.tags,
    enabled: skill.enabled,
    tool_ids: skill.tool_ids,
  }
}

export default function SkillForm({
  skill,
  tools,
  onClose,
  onSaved,
}: {
  /** 传 null 是新增 */
  skill: Skill | null
  /** 可选的工具（勾选进技能） */
  tools: Tool[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<FormState>(() => initialState(skill))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const toggleTool = (id: number, checked: boolean) =>
    set(
      'tool_ids',
      checked ? [...form.tool_ids, id] : form.tool_ids.filter((item) => item !== id),
    )

  const submit = async () => {
    if (!form.name.trim()) {
      setError('技能名不能为空')
      return
    }
    setSaving(true)
    setError('')
    const payload = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      knowledge: form.knowledge,
      checklist: form.checklist.filter((item) => item.trim()),
      examples: form.examples.filter((item) => item.trim()),
      constraints: form.constraints,
      tags: form.tags,
      enabled: form.enabled,
      tool_ids: form.tool_ids,
    }
    try {
      if (skill) {
        await updateSkill(skill.id, payload)
      } else {
        await createSkill(payload)
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
      title={skill ? `编辑技能：${skill.name}` : '新增技能'}
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

        <FormSection title="基本信息">
        <div className="grid gap-3.5 sm:grid-cols-2">
          <TextInput
            label="技能名"
            required
            value={form.name}
            onChange={(event) => set('name', event.target.value)}
            placeholder="如：代码审查"
          />
          <TextInput
            label="说明"
            value={form.description}
            onChange={(event) => set('description', event.target.value)}
            placeholder="一句话说清这个技能干什么"
          />
        </div>

        </FormSection>

        <FormSection title="知识正文" description="这些内容会进模型的提示词 —— 写清楚怎么做。">
        <TextArea
          label="核心做法"
          rows={5}
          value={form.knowledge}
          onChange={(event) => set('knowledge', event.target.value)}
          hint="挂载时这段会追加到系统提示词里 —— 写模型该怎么做"
        />

        <LinesField
          label="检查项"
          value={form.checklist}
          onChange={(value) => set('checklist', value)}
          hint="执行前后的检查清单，一行一条"
        />

        <LinesField
          label="正反例"
          value={form.examples}
          onChange={(value) => set('examples', value)}
          hint="一行一条"
        />

        <TextArea
          label="约束与禁区"
          rows={3}
          value={form.constraints}
          onChange={(event) => set('constraints', event.target.value)}
          hint="不能做什么 —— 比如「不要改测试文件」"
        />

        </FormSection>

        <FormSection title="标签与工具">
        <TagsField label="标签" value={form.tags} onChange={(value) => set('tags', value)} />

        <div>
          <div className="mb-1.5 text-xs font-medium text-ink-muted">包含的工具</div>
          {tools.length === 0 ? (
            <div className="rounded-lg border border-dashed border-line bg-sunken px-3 py-3 text-xs text-ink-faint">
              还没有工具可选
            </div>
          ) : (
            <div className="max-h-44 space-y-0.5 overflow-y-auto rounded-lg border border-line bg-sunken px-2 py-2">
              {tools.map((tool) => (
                <Checkbox
                  key={tool.id}
                  label={`${tool.name}${tool.display_name ? ` · ${tool.display_name}` : ''}`}
                  checked={form.tool_ids.includes(tool.id)}
                  onChange={(value) => toggleTool(tool.id, value)}
                />
              ))}
            </div>
          )}
        </div>

        <Toggle label="启用" checked={form.enabled} onChange={(value) => set('enabled', value)} />
        </FormSection>
      </div>
    </Modal>
  )
}
