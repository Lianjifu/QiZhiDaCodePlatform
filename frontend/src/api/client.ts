/**
 * 后端 API 客户端。
 *
 * 后端有两套响应体，成功与失败长得不一样：
 *
 * ```text
 * 成功  ResponseModel   { code: 0, message: "ok", data: T }
 * 失败  ErrorResponse   { code: 422, error_code: "VALIDATION_ERROR", message: "...", details: ... }
 * ```
 *
 * 这里把两者归一：**成功直接给 data，失败抛 {@link ApiError}**。调用方只需要
 * try/catch 一种失败形态。
 */

/** 后端统一响应包装。 */
export interface ResponseModel<T> {
  code: number
  message: string
  data: T | null
}

/** 后端统一错误响应。 */
export interface ErrorResponse {
  code: number
  error_code: string
  message: string
  details?: unknown
}

/** 接口前缀。走 Vite 代理，开发时转发到后端，生产同域。 */
export const API_BASE = '/api/v1'

/** 业务失败。HTTP 状态码、业务错误码、细节都在这儿。 */
export class ApiError extends Error {
  readonly status: number
  readonly errorCode: string
  readonly details?: unknown

  constructor(status: number, errorCode: string, message: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.errorCode = errorCode
    this.details = details
  }

  /** 能不能重试（网络问题、5xx）。 */
  get retryable(): boolean {
    return this.status >= 500 || this.status === 0
  }
}

/**
 * 注入请求头的位置。
 *
 * 平台目前**没有鉴权**（后端所有接口都是裸的），这里先留一个口子 —— 将来加
 * 登录/令牌，只改这一处，不用满项目找。
 */
type HeaderProvider = () => Record<string, string>

let headerProvider: HeaderProvider = () => ({})

/** 注册请求头提供方。 */
export function setHeaderProvider(provider: HeaderProvider): void {
  headerProvider = provider
}

/** 组装请求。 */
function buildInit(init: RequestInit = {}): RequestInit {
  return {
    ...init,
    headers: {
      ...headerProvider(),
      ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init.headers,
    },
  }
}

/** 把失败响应解析成 ApiError。 */
async function toApiError(response: Response): Promise<ApiError> {
  let body: Partial<ErrorResponse> = {}
  try {
    body = (await response.json()) as Partial<ErrorResponse>
  } catch {
    // 不是 JSON（比如网关返回了 HTML 错误页）—— 用状态码兜底
  }
  return new ApiError(
    response.status,
    body.error_code ?? 'UNKNOWN_ERROR',
    body.message ?? `请求失败（HTTP ${response.status}）`,
    body.details,
  )
}

/** 发一次请求，成功返回 data。 */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const body = await requestRaw<ResponseModel<T>>(path, init)
  return body.data as T
}

/**
 * 发一次请求，返回**整个**响应体。
 *
 * 给不套 ``ResponseModel`` 的接口用 —— 比如 ``/api/v1/health`` 返回的是裸
 * ``{"status": "ok", ...}``，没有 ``data`` 那一层。
 */
export async function requestRaw<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, buildInit(init))
  } catch (cause) {
    // 网络层失败（后端没起、断网）—— fetch 抛的是 TypeError，没有状态码
    throw new ApiError(0, 'NETWORK_ERROR', '连不上后端服务', cause)
  }
  if (!response.ok) {
    throw await toApiError(response)
  }
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string, init?: RequestInit) => request<T>(path, { ...init, method: 'GET' }),
  getRaw: <T>(path: string, init?: RequestInit) => requestRaw<T>(path, { ...init, method: 'GET' }),
  post: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, {
      ...init,
      method: 'POST',
      body: body instanceof FormData ? body : JSON.stringify(body ?? {}),
    }),
  put: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, {
      ...init,
      method: 'PUT',
      body: body instanceof FormData ? body : JSON.stringify(body ?? {}),
    }),
  delete: <T>(path: string, init?: RequestInit) => request<T>(path, { ...init, method: 'DELETE' }),
}
