/**
 * 与后端 schema 对应的类型。
 *
 * **改后端 schema 时记得同步改这里** —— 前端没有代码生成，两边靠人肉对齐。
 * 后端那边改动的落点：
 * - `backend/app/schemas/agent.py`
 * - `backend/app/schemas/files.py`
 * - `backend/app/schemas/llm_provider.py`
 * - `backend/app/schemas/common.py`
 */

/** 分页信息（后端 `PageMeta`）。 */
export interface PageMeta {
  total: number
  page: number
  page_size: number
  pages: number
}

/** 带分页的列表响应（后端 `*ListOut`）。 */
export interface PagedList<T> {
  items: T[]
  meta: PageMeta
}

// ---------- 编码助手 ----------

/** 一次工具调用的记录。 */
export interface ToolCall {
  name: string
  args: Record<string, unknown>
  tool_call_id?: string | null
  invocation_id?: number | null
  ok?: boolean | null
  status?: string | null
  error_code?: string | null
  duration_ms?: number | null
  blocked?: boolean | null
}

/** 一条待审批的调用。 */
export interface PendingApproval {
  kind: string
  tool: string
  arguments: Record<string, unknown>
  risk: string
  reasons: string[]
  message: string
}

/** 一轮对话的结果（后端 `AgentChatOut`）。 */
export interface AgentChatResult {
  thread_id: string
  content: string
  steps: number
  tool_calls: ToolCall[]
  status: 'completed' | 'pending_approval'
  pending: PendingApproval[]
}

/** 对话请求（后端 `AgentChatRequest`）。 */
export interface AgentChatRequest {
  message: string
  thread_id: string
  provider_id?: number | null
  skill_ids?: number[]
  attachments?: string[]
}

/** 工具执行结果的摘要（后端 `ToolResultOut`）。 */
export interface ToolResult {
  tool_call_id: string
  name: string
  ok: boolean
  status: string
  error_code?: string | null
  duration_ms: number
  summary: string
  /** 这次改动新增了几行（改文件的工具才有） */
  added?: number | null
  /** 这次改动删掉了几行（改文件的工具才有） */
  removed?: number | null
}

/** 流式接口的一帧（后端 `AgentStreamChunk`）。 */
export interface AgentStreamChunk {
  type: 'text' | 'tool_call' | 'tool_result' | 'pending_approval' | 'done' | 'error'
  text: string
  tool_call: ToolCall | null
  tool_result: ToolResult | null
  pending: PendingApproval[]
  status: string
  steps: number
  error?: string | null
}

/** 一条斜杠命令（后端 `SlashCommandOut`）。 */
export interface SlashCommand {
  name: string
  description: string
  kind: 'prompt' | 'action'
  template: string
  action: string
}

/** 历史消息里的一条（后端 `AgentHistoryMessage`）。 */
export interface AgentHistoryMessage {
  role: 'user' | 'assistant' | 'tool'
  content: string
  name?: string | null
  tool_call_id?: string | null
  tool_calls: ToolCall[]
  added?: number | null
  removed?: number | null
}

/** 一个会话的历史（后端 `AgentHistoryOut`）。 */
export interface AgentHistory {
  thread_id: string
  messages: AgentHistoryMessage[]
}

// ---------- 文件 ----------

/** 工作区里的一个文件（后端 `FileOut`）。 */
export interface WorkspaceFile {
  path: string
  filename: string
  size: number
  mime_type: string
  is_dir: boolean
  modified_at?: string | null
  uploaded: boolean
}

/** 上传结果（后端 `UploadOut`）。 */
export interface UploadedFile {
  path: string
  filename: string
  original_name: string
  size: number
  sha256: string
  mime_type: string
  is_image: boolean
  uploaded_at: string
}

/** 文件列表（后端 `FileListOut`）。 */
export interface FileList {
  files: WorkspaceFile[]
  total: number
}

// ---------- 会话状态 ----------

/** 上下文占用里的一块（后端 `ContextPartOut`）。 */
export interface ContextPart {
  key: string
  label: string
  tokens: number
  percent: number
  /** 可展开的明细（每个 MCP 工具、每个记忆文件） */
  items?: string[]
}

/** 上下文窗口用了多少（后端 `ContextUsageOut`）。**估算值，不是精确 token 数。 */
export interface ContextUsage {
  limit: number
  used: number
  percent: number
  free: number
  parts: ContextPart[]
}

/** 会话跑在什么环境里（后端 `EnvironmentOut`）。 */
export interface SessionEnvironment {
  thread_id: string
  workspace: string
  model: string
  provider: string
  trust_mode: string
}

/** 会话里的一个产出（后端 `OutputFileOut`）。 */
export interface OutputFile {
  path: string
  size: number
  is_dir: boolean
  uploaded: boolean
}

/** 这个会话一共改了多少行（后端 `ChangesOut`）。 */
export interface SessionChanges {
  added: number
  removed: number
}

/** 接进来的外部东西（后端 `ConnectorOut`）。 */
export interface Connector {
  name: string
  kind: string
  status: string
  detail: string
}

/** 一个正在跑的工具调用（后端 `RunningTaskOut`）。 */
export interface RunningTask {
  tool: string
  detail: string
  elapsed_ms: number
}

/** 会话状态（后端 `SessionStatusOut`）。 */
export interface SessionStatus {
  thread_id: string
  environment: SessionEnvironment
  context: ContextUsage
  outputs: OutputFile[]
  changes: SessionChanges
  connectors: Connector[]
  /** 正在跑的工具调用 */
  tasks: RunningTask[]
  /** 这个会话跑完过多少个工具调用 */
  finished_tasks: number
  /** 这份状态是什么时候读的 */
  updated_at: string
}

// ---------- 模型接入配置 ----------

/** 一个供应商配置（后端 `LLMProviderOut`）。 */
export interface LLMProvider {
  id: number
  name: string
  provider: string
  base_url: string | null
  default_model: string
  available_models: string[]
  embedding_model: string
  embedding_dim: number
  temperature: number
  max_tokens: number
  context_window: number
  vision_enabled: boolean
  timeout: number
  is_default: boolean
  is_active: boolean
  description: string | null
  api_key_masked: string
  has_api_key: boolean
  created_at: string
  updated_at: string
}

// ---------- 能力 ----------

/** 能力清单里的一项（后端 `CapabilityOut`）。 */
export interface Capability {
  key: string
  kind: string
  source_type: string
  display_name?: string
  description?: string
  enabled?: boolean
  risk?: string
  permission?: string
}

/** 能力清单（后端 `CapabilityListOut`）。 */
export interface CapabilityList {
  items: Capability[]
  total: number
  kinds: string[]
}
