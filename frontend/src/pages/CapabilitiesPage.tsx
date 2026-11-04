/**
 * 能力清单。
 *
 * 四个标签页：
 *
 * | 标签 | 内容 |
 * | --- | --- |
 * | 清单 | agent 实际能看到的能力（后端 `/capabilities` 汇总） |
 * | 工具 | 原子能力 —— 增删改查、测可用性 |
 * | 技能 | 能力组合 —— 增删改查 |
 * | MCP | 外部 MCP 服务器 —— 增删改查、探测工具 |
 *
 * **"清单"是只读的** —— 它是 agent 视角的汇总；要改就去对应的标签页。
 */
import { useCallback, useEffect, useState } from 'react'

import {
  deleteMCPServer,
  deleteSkill,
  deleteTool,
  listCapabilities,
  listBuiltinTools,
  listMCPServers,
  listSkills,
  listTools,
  probeMCPServer,
  testTool,
} from '../api/resources'
import Badge from '../components/ui/Badge'
import Banner from '../components/ui/Banner'
import Button from '../components/ui/Button'
import DataTable, { type Column } from '../components/ui/DataTable'
import EmptyState from '../components/ui/EmptyState'
import Modal from '../components/ui/Modal'
import PageHeader from '../components/ui/PageHeader'
import Tabs from '../components/ui/Tabs'
import { useConfirm } from '../components/ui/useConfirm'
import MCPForm from '../features/capabilities/MCPForm'
import { approvalSummary } from '../features/capabilities/risk'
import SkillForm from '../features/capabilities/SkillForm'
import ToolForm from '../features/capabilities/ToolForm'
import type {
  BuiltinTool,
  Capability,
  MCPServer,
  MCPServerTool,
  Skill,
  Tool,
} from '../types/api'

type Tab = 'overview' | 'tools' | 'skills' | 'mcp'

export default function CapabilitiesPage() {
  const { confirm, dialog: confirmDialog } = useConfirm()
  const [tab, setTab] = useState<Tab>('overview')
  const [overview, setOverview] = useState<Capability[]>([])
  const [kinds, setKinds] = useState<string[]>([])
  const [tools, setTools] = useState<Tool[]>([])
  const [builtins, setBuiltins] = useState<BuiltinTool[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [servers, setServers] = useState<MCPServer[]>([])
  const [error, setError] = useState('')

  /** 弹窗状态：undefined = 不开，null = 新增，有值 = 编辑那一条 */
  const [toolEditing, setToolEditing] = useState<Tool | null | undefined>(undefined)
  const [skillEditing, setSkillEditing] = useState<Skill | null | undefined>(undefined)
  const [mcpEditing, setMcpEditing] = useState<MCPServer | null | undefined>(undefined)
  const [viewing, setViewing] = useState<Skill | Tool | MCPServer | null>(null)
  const [probed, setProbed] = useState<MCPServerTool[] | null>(null)
  const [note, setNote] = useState('')

  /** 读数据。分开写是为了让 effect 里的 setState 落在 then 里（同步调用会被 lint 判成级联渲染）。 */
  const load = useCallback(async () => {
    const [list, toolPage, skillPage, serverList, builtinList] = await Promise.all([
      listCapabilities(),
      listTools(),
      listSkills(),
      listMCPServers(),
      listBuiltinTools(),
    ])
    return { list, toolPage, skillPage, serverList, builtinList }
  }, [])

  const apply = (data: {
    list: Awaited<ReturnType<typeof listCapabilities>>
    toolPage: Awaited<ReturnType<typeof listTools>>
    skillPage: Awaited<ReturnType<typeof listSkills>>
    serverList: Awaited<ReturnType<typeof listMCPServers>>
    builtinList: Awaited<ReturnType<typeof listBuiltinTools>>
  }) => {
    setOverview(data.list.items)
    setKinds(data.list.kinds)
    setTools(data.toolPage.items)
    setSkills(data.skillPage.items)
    setServers(data.serverList)
    setBuiltins(data.builtinList)
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

  const toolColumns: Column<Tool>[] = [
    {
      header: '名称',
      mobile: 'title',
      render: (tool) => (
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-ink">{tool.name}</span>
          {tool.display_name ? (
            <span className="text-xs text-ink-muted">{tool.display_name}</span>
          ) : null}
          {tool.origin === 'builtin' ? <Badge>内置</Badge> : null}
        </span>
      ),
    },
    {
      header: '权限 / 风险',
      mobile: 'subtitle',
      render: (tool) => (
        <span className="text-xs text-ink-muted">
          {tool.permission === 'confirm' ? '要人批 · ' : ''}
          {approvalSummary(tool.risk)}
        </span>
      ),
    },
    {
      header: '类型',
      mobile: 'meta',
      render: (tool) => <span className="text-xs text-ink-muted">{tool.type}</span>,
    },
    {
      header: '状态',
      render: (tool) =>
        tool.enabled ? <Badge tone="success">启用</Badge> : <Badge>停用</Badge>,
    },
  ]

  const skillColumns: Column<Skill>[] = [
    {
      header: '名称',
      mobile: 'title',
      render: (skill) => (
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-ink">{skill.name}</span>
          {skill.builtin ? <Badge>内置</Badge> : null}
        </span>
      ),
    },
    {
      header: '说明',
      mobile: 'subtitle',
      render: (skill) => (
        <span className="text-xs text-ink-muted">{skill.description || '—'}</span>
      ),
    },
    {
      header: '工具',
      mobile: 'meta',
      render: (skill) => (
        <span className="text-xs text-ink-muted">{skill.tool_names.join('、') || '—'}</span>
      ),
    },
    {
      header: '状态',
      render: (skill) =>
        skill.enabled ? <Badge tone="success">启用</Badge> : <Badge>停用</Badge>,
    },
  ]

  const mcpColumns: Column<MCPServer>[] = [
    {
      header: '名称',
      mobile: 'title',
      render: (server) => <span className="text-sm font-medium text-ink">{server.name}</span>,
    },
    {
      header: '地址 / 命令',
      mobile: 'subtitle',
      render: (server) => (
        <span className="font-mono text-xs text-ink-muted">
          {server.url ?? server.command ?? '—'}
        </span>
      ),
    },
    {
      header: '方式',
      mobile: 'meta',
      render: (server) => <span className="text-xs text-ink-muted">{server.transport}</span>,
    },
    {
      header: '状态',
      render: (server) =>
        server.enabled ? <Badge tone="success">启用</Badge> : <Badge>停用</Badge>,
    },
  ]

  const actionButton =
    tab === 'tools' ? (
      <Button variant="primary" size="lg" onClick={() => setToolEditing(null)}>
        新增工具
      </Button>
    ) : tab === 'skills' ? (
      <Button variant="primary" size="lg" onClick={() => setSkillEditing(null)}>
        新增技能
      </Button>
    ) : tab === 'mcp' ? (
      <Button variant="primary" size="lg" onClick={() => setMcpEditing(null)}>
        新增 MCP 服务器
      </Button>
    ) : null

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6 sm:py-6">
      <PageHeader
        title="能力清单"
        description="工具、技能、MCP 都在这一张清单里 —— agent 只从注册层取能力，不认别的来源。"
        meta={kinds.length > 0 ? kinds.join(' / ') : undefined}
        actions={actionButton}
      />

      <div className="mt-4">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { key: 'overview', label: '清单', count: overview.length },
            { key: 'tools', label: '工具', count: tools.length },
            { key: 'skills', label: '技能', count: skills.length },
            { key: 'mcp', label: 'MCP', count: servers.length },
          ]}
        />
      </div>

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

      {tab === 'overview' ? (
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {overview.length === 0 ? (
            <div className="sm:col-span-2">
              <EmptyState title="清单是空的" description="去「工具」「技能」或「MCP」标签页加一条。" />
            </div>
          ) : (
            overview.map((item) => (
              <div
                key={`${item.kind}-${item.key}`}
                className="rounded-xl border border-line bg-raised px-4 py-3 shadow-1"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge>{item.kind}</Badge>
                  <span className="font-mono text-sm text-ink">{item.key}</span>
                  {item.display_name ? (
                    <span className="text-sm text-ink-muted">{item.display_name}</span>
                  ) : null}
                  {item.enabled === false ? <Badge tone="warning">已停用</Badge> : null}
                </div>
                {item.description ? (
                  <div className="mt-1.5 text-xs leading-relaxed text-ink-muted">
                    {item.description}
                  </div>
                ) : null}
              </div>
            ))
          )}
        </div>
      ) : null}

      {tab === 'tools' ? (
        <div className="mt-4 overflow-hidden rounded-xl border border-line bg-raised shadow-1">
          <DataTable
            columns={toolColumns}
            rows={tools}
            rowKey={(tool) => tool.id}
            actions={(tool) => (
              <>
                <Button size="sm" variant="ghost" onClick={() => setViewing(tool)}>
                  详情
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    void run(async () => {
                      const result = await testTool(tool.id)
                      setNote(
                        result.ok
                          ? `工具 ${tool.name} 可用：${result.message || '通过'}`
                          : `工具 ${tool.name} 不可用：${result.message}`,
                      )
                    }, '')
                  }
                >
                  测试
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setToolEditing(tool)}>
                  编辑
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    void (async () => {
                      const ok = await confirm({
                        title: `删掉工具「${tool.name}」？`,
                        description: '技能里对它的引用会一并清掉。',
                        confirmText: '删掉',
                        danger: true,
                      })
                      if (!ok) return
                      await run(() => deleteTool(tool.id), `已删掉工具 ${tool.name}`)
                    })()
                  }}
                >
                  <span className="text-danger">删除</span>
                </Button>
              </>
            )}
            empty={
              <EmptyState
                title="还没有工具"
                description="工具是 agent 能调用的原子能力 —— 从内置白名单里挑一个接进来。"
                action={
                  <Button variant="primary" onClick={() => setToolEditing(null)}>
                    新增工具
                  </Button>
                }
              />
            }
          />
        </div>
      ) : null}

      {tab === 'skills' ? (
        <div className="mt-4 overflow-hidden rounded-xl border border-line bg-raised shadow-1">
          <DataTable
            columns={skillColumns}
            rows={skills}
            rowKey={(skill) => skill.id}
            actions={(skill) => (
              <>
                <Button size="sm" variant="ghost" onClick={() => setViewing(skill)}>
                  详情
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setSkillEditing(skill)}>
                  编辑
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    void (async () => {
                      const ok = await confirm({
                        title: `删掉技能「${skill.name}」？`,
                        description: '技能里的知识与成员关系会一并没了。',
                        confirmText: '删掉',
                        danger: true,
                      })
                      if (!ok) return
                      await run(() => deleteSkill(skill.id), `已删掉技能 ${skill.name}`)
                    })()
                  }}
                >
                  <span className="text-danger">删除</span>
                </Button>
              </>
            )}
            empty={
              <EmptyState
                title="还没有技能"
                description="技能是一组工具加上知识 —— 模型按描述挑着用。"
                action={
                  <Button variant="primary" onClick={() => setSkillEditing(null)}>
                    新增技能
                  </Button>
                }
              />
            }
          />
        </div>
      ) : null}

      {tab === 'mcp' ? (
        <div className="mt-4 overflow-hidden rounded-xl border border-line bg-raised shadow-1">
          <DataTable
            columns={mcpColumns}
            rows={servers}
            rowKey={(server) => server.id}
            actions={(server) => (
              <>
                <Button size="sm" variant="ghost" onClick={() => setViewing(server)}>
                  详情
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    void run(async () => {
                      setProbed(await probeMCPServer(server.id))
                    }, '')
                  }
                >
                  探测工具
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setMcpEditing(server)}>
                  编辑
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    void (async () => {
                      const ok = await confirm({
                        title: `删掉 MCP 服务器「${server.name}」？`,
                        description: '通过它接进来的工具会一起消失。',
                        confirmText: '删掉',
                        danger: true,
                      })
                      if (!ok) return
                      await run(
                        () => deleteMCPServer(server.id),
                        `已删掉 MCP 服务器 ${server.name}`,
                      )
                    })()
                  }}
                >
                  <span className="text-danger">删除</span>
                </Button>
              </>
            )}
            empty={
              <EmptyState
                title="还没有 MCP 服务器"
                description="接一个 MCP 服务器，服务器上的工具就进清单了。"
                action={
                  <Button variant="primary" onClick={() => setMcpEditing(null)}>
                    新增 MCP 服务器
                  </Button>
                }
              />
            }
          />
        </div>
      ) : null}

      {toolEditing !== undefined ? (
        <ToolForm
          tool={toolEditing}
          builtins={builtins}
          servers={servers}
          onClose={() => setToolEditing(undefined)}
          onSaved={() => {
            setToolEditing(undefined)
            setNote('已保存工具')
            void refresh()
          }}
        />
      ) : null}

      {skillEditing !== undefined ? (
        <SkillForm
          skill={skillEditing}
          tools={tools}
          onClose={() => setSkillEditing(undefined)}
          onSaved={() => {
            setSkillEditing(undefined)
            setNote('已保存技能')
            void refresh()
          }}
        />
      ) : null}

      {mcpEditing !== undefined ? (
        <MCPForm
          server={mcpEditing}
          onClose={() => setMcpEditing(undefined)}
          onSaved={() => {
            setMcpEditing(undefined)
            setNote('已保存 MCP 服务器')
            void refresh()
          }}
        />
      ) : null}

      {viewing ? (
        <Modal title="详情" onClose={() => setViewing(null)} size="lg">
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-lg bg-sunken p-3 font-mono text-xs leading-relaxed text-ink-muted">
            {JSON.stringify(viewing, null, 2)}
          </pre>
        </Modal>
      ) : null}

      {probed ? (
        <Modal title="服务器上的工具" onClose={() => setProbed(null)}>
          {probed.length === 0 ? (
            <div className="text-sm text-ink-faint">这个服务器上没有工具</div>
          ) : (
            <ul className="space-y-2">
              {probed.map((tool) => (
                <li key={tool.name}>
                  <div className="font-mono text-sm text-ink">{tool.name}</div>
                  {tool.description ? (
                    <div className="text-xs text-ink-muted">{tool.description}</div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Modal>
      ) : null}

      {confirmDialog}
    </div>
  )
}
