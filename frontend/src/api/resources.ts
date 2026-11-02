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
  AgentHistory,
  CapabilityList,
  FileList,
  LLMProvider,
  PagedList,
  SessionStatus,
  SlashCommand,
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
