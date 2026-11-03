/**
 * 平台各资源的后端接口。
 *
 * 按资源分文件 —— 一个后端 router 对应一个文件。页面不要直接调 `api.get`，
 * 走这里，路径与参数都收在一处。
 */

import { api, API_BASE } from './client'
import type {
  AgentChatRequest,
  AgentChatResult,
  AgentForkResult,
  AgentHistory,
  BuiltinTool,
  CapabilityList,
  Environment,
  EnvironmentBinding,
  EnvironmentList,
  FileList,
  LLMProvider,
  MCPServer,
  MCPServerTool,
  PagedList,
  ProviderPreset,
  ProviderTestResult,
  SessionStatus,
  Skill,
  SlashCommand,
  Tool,
  ToolTestResult,
  UploadedFile,
} from '../types/api'

// ---------- 健康检查 ----------

/** 健康检查。后端返回的是裸对象，没有 ResponseModel 那一层。 */
export function health(): Promise<{ status: string; version: string }> {
  return api.getRaw<{ status: string; version: string }>('/health')
}

// ---------- 模型接入配置 ----------

export function listProviders(): Promise<PagedList<LLMProvider>> {
  return api.get<PagedList<LLMProvider>>('/llm-providers')
}

export function getProvider(id: number): Promise<LLMProvider> {
  return api.get<LLMProvider>(`/llm-providers/${id}`)
}

/** 供应商预设（新建时厂商下拉用）。 */
export function listProviderPresets(): Promise<ProviderPreset[]> {
  return api.get<ProviderPreset[]>('/llm-providers/presets')
}

/** 新建供应商配置。 */
export function createProvider(payload: Record<string, unknown>): Promise<LLMProvider> {
  return api.post<LLMProvider>('/llm-providers', payload)
}

/** 改供应商配置。 */
export function updateProvider(id: number, payload: Record<string, unknown>): Promise<LLMProvider> {
  return api.put<LLMProvider>(`/llm-providers/${id}`, payload)
}

/** 删供应商配置。 */
export function deleteProvider(id: number): Promise<void> {
  return api.delete<void>(`/llm-providers/${id}`)
}

/** 测试连通性。 */
export function testProvider(id: number): Promise<ProviderTestResult> {
  return api.post<ProviderTestResult>(`/llm-providers/${id}/test`)
}

// ---------- 工具 ----------

export function listTools(): Promise<PagedList<Tool>> {
  return api.get<PagedList<Tool>>('/tools')
}

/** 内置能力白名单 —— builtin 类型工具的来源从这里选。 */
export function listBuiltinTools(): Promise<BuiltinTool[]> {
  return api.get<BuiltinTool[]>('/tools/builtins')
}

export function createTool(payload: Record<string, unknown>): Promise<Tool> {
  return api.post<Tool>('/tools', payload)
}

export function updateTool(id: number, payload: Record<string, unknown>): Promise<Tool> {
  return api.put<Tool>(`/tools/${id}`, payload)
}

export function deleteTool(id: number): Promise<void> {
  return api.delete<void>(`/tools/${id}`)
}

/** 测试工具配置能不能跑通。 */
export function testTool(id: number): Promise<ToolTestResult> {
  return api.post<ToolTestResult>(`/tools/${id}/test`)
}

// ---------- 技能 ----------

export function listSkills(): Promise<PagedList<Skill>> {
  return api.get<PagedList<Skill>>('/skills')
}

export function createSkill(payload: Record<string, unknown>): Promise<Skill> {
  return api.post<Skill>('/skills', payload)
}

export function updateSkill(id: number, payload: Record<string, unknown>): Promise<Skill> {
  return api.put<Skill>(`/skills/${id}`, payload)
}

export function deleteSkill(id: number): Promise<void> {
  return api.delete<void>(`/skills/${id}`)
}

// ---------- MCP 服务器 ----------

export function listMCPServers(): Promise<MCPServer[]> {
  return api.get<MCPServer[]>('/mcp-servers')
}

export function createMCPServer(payload: Record<string, unknown>): Promise<MCPServer> {
  return api.post<MCPServer>('/mcp-servers', payload)
}

export function updateMCPServer(id: number, payload: Record<string, unknown>): Promise<MCPServer> {
  return api.put<MCPServer>(`/mcp-servers/${id}`, payload)
}

export function deleteMCPServer(id: number): Promise<void> {
  return api.delete<void>(`/mcp-servers/${id}`)
}

/** 探测 MCP 服务器上有哪些工具。 */
export function probeMCPServer(id: number): Promise<MCPServerTool[]> {
  return api.get<MCPServerTool[]>(`/mcp-servers/${id}/tools`)
}

// ---------- 能力 ----------

export function listCapabilities(): Promise<CapabilityList> {
  return api.get<CapabilityList>('/capabilities')
}

// ---------- 编码助手 ----------

/** 跑一轮对话，等它跑完。 */
export function chat(payload: AgentChatRequest): Promise<AgentChatResult> {
  return api.post<AgentChatResult>('/agent/chat', payload)
}

/**
 * 流式对话。返回原始响应，交给 {@link readSSE} 逐帧读。
 *
 * 不能走 `api.post` —— 它会把响应体整个 parse 成 JSON，流就没了。
 */
export function streamChat(payload: AgentChatRequest, signal?: AbortSignal): Promise<Response> {
  return fetch(`${API_BASE}/agent/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
}

/** 读会话历史消息（刷新页面后靠它还原对话）。 */
export function history(threadId: string): Promise<AgentHistory> {
  return api.get<AgentHistory>(`/agent/${threadId}/history`)
}

/** 读会话状态（环境、上下文占用、产出、改动量）。 */
export function sessionStatus(threadId: string): Promise<SessionStatus> {
  return api.get<SessionStatus>(`/agent/${threadId}/status`)
}

/** 取某个会话当前挂着的待审批调用。 */
export function pendingApprovals(threadId: string): Promise<AgentChatResult['pending']> {
  return api.get<AgentChatResult['pending']>(`/agent/${threadId}/pending`)
}

/** 批准挂起的调用，图继续跑。 */
export function approve(threadId: string): Promise<AgentChatResult> {
  return api.post<AgentChatResult>(`/agent/${threadId}/approve`)
}

/** 拒绝挂起的调用。 */
export function reject(threadId: string): Promise<AgentChatResult> {
  return api.post<AgentChatResult>(`/agent/${threadId}/reject`)
}

/** 斜杠命令清单（输入框敲 `/` 时弹的那份）。 */
export function listCommands(): Promise<SlashCommand[]> {
  return api.get<SlashCommand[]>('/agent/commands')
}

/** 清空会话的对话记录（删掉 checkpoint 里的历史）。 */
export function clearConversation(threadId: string): Promise<{ thread_id: string; status: string }> {
  return api.delete<{ thread_id: string; status: string }>(`/agent/${threadId}`)
}

/**
 * 重新生成上一轮（流式）。
 *
 * 后端把最后一条用户消息起往后全删，再用那条消息重跑 —— 帧格式与
 * {@link streamChat} 完全一致。
 */
export function regenerateChat(
  threadId: string,
  payload: { provider_id?: number | null; skill_ids?: number[] } = {},
  signal?: AbortSignal,
): Promise<Response> {
  return fetch(`${API_BASE}/agent/${threadId}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
}

/** 从某条消息分支出新会话。 */
export function forkConversation(
  threadId: string,
  messageId: string,
): Promise<AgentForkResult> {
  return api.post<AgentForkResult>(`/agent/${threadId}/fork`, { message_id: messageId })
}

// ---------- 工作目录（环境） ----------

/** 列出可作为工作目录的环境。 */
export function listEnvironments(): Promise<EnvironmentList> {
  return api.get<EnvironmentList>('/environments')
}

/** 从一个仓库派生新的 worktree（独立检出 + 新分支）。 */
export function createWorktree(payload: {
  repo_path: string
  /** 分支名；不给就自动生成 */
  branch?: string
  base?: string | null
}): Promise<Environment> {
  return api.post<Environment>('/environments', payload)
}

/** 删掉一个平台创建的 worktree。 */
export function deleteWorktree(path: string): Promise<{ path: string; status: string }> {
  return api.delete<{ path: string; status: string }>(
    `/environments?path=${encodeURIComponent(path)}`,
  )
}

/** 看会话当前绑在工作目录上。 */
export function getEnvironmentBinding(threadId: string): Promise<EnvironmentBinding> {
  return api.get<EnvironmentBinding>(`/environments/binding/${encodeURIComponent(threadId)}`)
}

/** 把会话绑到某个工作目录。 */
export function bindEnvironment(
  threadId: string,
  path: string,
  label?: string,
): Promise<EnvironmentBinding> {
  return api.post<EnvironmentBinding>(`/environments/binding/${encodeURIComponent(threadId)}`, {
    path,
    label: label ?? '',
  })
}

/** 解绑（回到平台自带的会话工作区）。 */
export function unbindEnvironment(threadId: string): Promise<{ thread_id: string; status: string }> {
  return api.delete<{ thread_id: string; status: string }>(
    `/environments/binding/${encodeURIComponent(threadId)}`,
  )
}

// ---------- 文件 ----------

/** 列出工作区文件。 */
export function listFiles(threadId: string): Promise<FileList> {
  return api.get<FileList>(`/files?thread_id=${encodeURIComponent(threadId)}`)
}

/** 列出工作区的文件**与目录**（@ 提及面板用）。 */
export function listWorkspaceTree(threadId: string): Promise<FileList> {
  return api.get<FileList>(`/files/tree?thread_id=${encodeURIComponent(threadId)}`)
}

/**
 * 上传文件。
 *
 * 用 FormData —— base64 编码会膨胀 33%，大文件尤其明显。
 */
export function uploadFile(threadId: string, file: File): Promise<UploadedFile> {
  const form = new FormData()
  form.append('file', file)
  return api.post<UploadedFile>(`/files?thread_id=${encodeURIComponent(threadId)}`, form)
}

/** 下载文件的地址（交给浏览器自己去取）。 */
export function downloadUrl(threadId: string, path: string): string {
  return `${API_BASE}/files/download?thread_id=${encodeURIComponent(threadId)}&path=${encodeURIComponent(path)}`
}

/** 打包下载的地址（整个工作区打成一个 zip）。 */
export function archiveUrl(threadId: string): string {
  return `${API_BASE}/files/archive?thread_id=${encodeURIComponent(threadId)}`
}

/** 删除文件。 */
export function deleteFile(threadId: string, path: string): Promise<{ path: string; status: string }> {
  return api.delete<{ path: string; status: string }>(
    `/files?thread_id=${encodeURIComponent(threadId)}&path=${encodeURIComponent(path)}`,
  )
}

/**
 * 保存文件内容（覆盖）。
 *
 * 只处理文本 —— 二进制走上传。
 */
export function saveFile(
  threadId: string,
  path: string,
  content: string,
): Promise<{ path: string; status: string; size: string }> {
  return api.put<{ path: string; status: string; size: string }>(
    `/files?thread_id=${encodeURIComponent(threadId)}&path=${encodeURIComponent(path)}`,
    { content },
  )
}

/** 重命名（或移动）文件。 */
export function renameFile(
  threadId: string,
  path: string,
  newPath: string,
): Promise<{ path: string; status: string }> {
  return api.patch<{ path: string; status: string }>(
    `/files?thread_id=${encodeURIComponent(threadId)}&path=${encodeURIComponent(path)}`,
    { new_path: newPath },
  )
}

/**
 * 读一个文件的文本内容。
 *
 * 下载接口是给浏览器直接取文件的（带 attachment 头），这里自己 fetch 一份
 * 回来当文本用 —— 预览与编辑都要。
 */
export async function readFileText(threadId: string, path: string): Promise<string> {
  const response = await fetch(
    `${API_BASE}/files/download?thread_id=${encodeURIComponent(threadId)}&path=${encodeURIComponent(path)}`,
  )
  if (!response.ok) {
    throw new Error(`读不到文件（HTTP ${response.status}）`)
  }
  return response.text()
}
