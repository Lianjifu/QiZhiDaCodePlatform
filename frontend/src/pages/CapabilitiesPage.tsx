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
import Button from '../components/ui/Button'
import Modal from '../components/ui/Modal'
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

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '清单' },
  { key: 'tools', label: '工具' },
  { key: 'skills', label: '技能' },
  { key: 'mcp', label: 'MCP' },
]

export default function CapabilitiesPage() {
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

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">能力清单</h1>
          <p className="mt-1 text-sm text-ink-muted">
            工具、技能、MCP 都在这一张清单里 —— agent 只从注册层取能力，不认别的来源。
            {kinds.length > 0 && `（${kinds.join(' / ')}）`}
          </p>
        </div>
        {tab === 'tools' ? (
          <Button variant="primary" onClick={() => setToolEditing(null)}>
            新增工具
          </Button>
        ) : tab === 'skills' ? (
          <Button variant="primary" onClick={() => setSkillEditing(null)}>
            新增技能
          </Button>
        ) : tab === 'mcp' ? (
          <Button variant="primary" onClick={() => setMcpEditing(null)}>
            新增 MCP 服务器
          </Button>
        ) : null}
      </div>

      <div className="mt-4 flex gap-1 border-b border-line">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setTab(item.key)}
            className={[
              '-mb-px border-b-2 px-3 py-1.5 text-sm transition-colors',
              tab === item.key
                ? 'border-accent text-ink'
                : 'border-transparent text-ink-muted hover:text-ink',
            ].join(' ')}
          >
            {item.label}
          </button>
        ))}
      </div>

      {error ? (
        <div className="mt-4 rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </div>
      ) : null}
      {note ? (
        <div className="mt-4 rounded-lg border border-success bg-success-soft px-3 py-2 text-sm text-success">
          {note}
        </div>
      ) : null}

      {tab === 'overview' ? (
        <div className="mt-4 space-y-2">
          {overview.length === 0 ? (
            <Empty text="清单是空的" />
          ) : (
            overview.map((item) => (
              <div
                key={`${item.kind}-${item.key}`}
                className="rounded-lg border border-line bg-raised px-4 py-3"
              >
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
      ) : null}

      {tab === 'tools' ? (
        <div className="mt-4 overflow-hidden rounded-lg border border-line bg-raised">
          {tools.length === 0 ? (
            <Empty text="还没有工具" />
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-sunken text-left text-xs text-ink-faint">
                <tr>
                  <th className="px-4 py-2 font-medium">名称</th>
                  <th className="px-4 py-2 font-medium">类型</th>
                  <th className="px-4 py-2 font-medium">权限 / 风险</th>
                  <th className="px-4 py-2 font-medium">状态</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {tools.map((tool) => (
                  <tr key={tool.id} className="border-t border-line">
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-ink">{tool.name}</span>
                      {tool.display_name && (
                        <span className="ml-2 text-xs text-ink-muted">{tool.display_name}</span>
                      )}
                      {tool.origin === 'builtin' && (
                        <span className="ml-2 rounded bg-sunken px-1.5 py-0.5 text-[10px] text-ink-faint">
                          内置
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-xs text-ink-muted">{tool.type}</td>
                    <td className="px-4 py-2 text-xs text-ink-muted">
                      {tool.permission === 'confirm' ? '要人批 · ' : ''}
                      {approvalSummary(tool.risk)}
                    </td>
                    <td className="px-4 py-2 text-xs">
                      {tool.enabled ? (
                        <span className="text-success">启用</span>
                      ) : (
                        <span className="text-ink-faint">停用</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setViewing(tool)}
                          className="text-xs text-ink-faint hover:text-ink"
                        >
                          详情
                        </button>
                        <button
                          type="button"
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
                          className="text-xs text-ink-faint hover:text-accent"
                        >
                          测试
                        </button>
                        <button
                          type="button"
                          onClick={() => setToolEditing(tool)}
                          className="text-xs text-accent hover:underline"
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (!window.confirm(`删掉工具「${tool.name}」？`)) return
                            void run(() => deleteTool(tool.id), `已删掉工具 ${tool.name}`)
                          }}
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
      ) : null}

      {tab === 'skills' ? (
        <div className="mt-4 overflow-hidden rounded-lg border border-line bg-raised">
          {skills.length === 0 ? (
            <Empty text="还没有技能" />
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-sunken text-left text-xs text-ink-faint">
                <tr>
                  <th className="px-4 py-2 font-medium">名称</th>
                  <th className="px-4 py-2 font-medium">说明</th>
                  <th className="px-4 py-2 font-medium">工具</th>
                  <th className="px-4 py-2 font-medium">状态</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {skills.map((skill) => (
                  <tr key={skill.id} className="border-t border-line">
                    <td className="px-4 py-2">
                      <span className="text-ink">{skill.name}</span>
                      {skill.builtin && (
                        <span className="ml-2 rounded bg-sunken px-1.5 py-0.5 text-[10px] text-ink-faint">
                          内置
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-xs text-ink-muted">{skill.description || '—'}</td>
                    <td className="px-4 py-2 text-xs text-ink-muted">
                      {skill.tool_names.join('、') || '—'}
                    </td>
                    <td className="px-4 py-2 text-xs">
                      {skill.enabled ? (
                        <span className="text-success">启用</span>
                      ) : (
                        <span className="text-ink-faint">停用</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setViewing(skill)}
                          className="text-xs text-ink-faint hover:text-ink"
                        >
                          详情
                        </button>
                        <button
                          type="button"
                          onClick={() => setSkillEditing(skill)}
                          className="text-xs text-accent hover:underline"
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (!window.confirm(`删掉技能「${skill.name}」？`)) return
                            void run(() => deleteSkill(skill.id), `已删掉技能 ${skill.name}`)
                          }}
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
      ) : null}

      {tab === 'mcp' ? (
        <div className="mt-4 overflow-hidden rounded-lg border border-line bg-raised">
          {servers.length === 0 ? (
            <Empty text="还没有 MCP 服务器" />
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-sunken text-left text-xs text-ink-faint">
                <tr>
                  <th className="px-4 py-2 font-medium">名称</th>
                  <th className="px-4 py-2 font-medium">方式</th>
                  <th className="px-4 py-2 font-medium">地址 / 命令</th>
                  <th className="px-4 py-2 font-medium">状态</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {servers.map((server) => (
                  <tr key={server.id} className="border-t border-line">
                    <td className="px-4 py-2 text-ink">{server.name}</td>
                    <td className="px-4 py-2 text-xs text-ink-muted">{server.transport}</td>
                    <td className="px-4 py-2 font-mono text-xs text-ink-muted">
                      {server.url ?? server.command ?? '—'}
                    </td>
                    <td className="px-4 py-2 text-xs">
                      {server.enabled ? (
                        <span className="text-success">启用</span>
                      ) : (
                        <span className="text-ink-faint">停用</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setViewing(server)}
                          className="text-xs text-ink-faint hover:text-ink"
                        >
                          详情
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            void run(async () => {
                              setProbed(await probeMCPServer(server.id))
                            }, '')
                          }
                          className="text-xs text-ink-faint hover:text-accent"
                        >
                          探测工具
                        </button>
                        <button
                          type="button"
                          onClick={() => setMcpEditing(server)}
                          className="text-xs text-accent hover:underline"
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (!window.confirm(`删掉 MCP 服务器「${server.name}」？`)) return
                            void run(
                              () => deleteMCPServer(server.id),
                              `已删掉 MCP 服务器 ${server.name}`,
                            )
                          }}
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
        <Modal title="详情" onClose={() => setViewing(null)} wide>
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
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return (
    <div className="px-4 py-10 text-center text-sm text-ink-faint">{text}</div>
  )
}
