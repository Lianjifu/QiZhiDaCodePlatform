/**
 * SSE（Server-Sent Events）读取器。
 *
 * 后端的流式接口推的帧长这样：
 *
 * ```text
 * data: {"text":"增量"}
 * data: [DONE]
 * ```
 *
 * 帧之间用空行分隔，最后一帧是 `[DONE]` 表示结束。所以解析要**攒够一整帧**
 * 再处理 —— 一段 TCP 数据里可能只有半帧。
 */

import { ApiError } from './client'

/** 从一段原始文本里取出所有 data 载荷。 */
function extractData(frame: string): string[] {
  return frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .filter((payload) => payload.length > 0)
}

/**
 * 读一条 SSE 流，逐帧产出。
 *
 * @param response  已经 fetch 过的响应。
 * @param parse     把一帧的载荷转成对象；默认按 JSON 解析。
 */
export async function* readSSE<T>(
  response: Response,
  parse: (payload: string) => T = (payload) => JSON.parse(payload) as T,
): AsyncGenerator<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(
      response.status,
      body?.error_code ?? 'STREAM_ERROR',
      body?.message ?? `流式请求失败（HTTP ${response.status}）`,
    )
  }
  if (!response.body) {
    throw new ApiError(0, 'NO_BODY', '响应没有可读流')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // 一帧一帧地切出来
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        for (const payload of extractData(frame)) {
          if (payload === '[DONE]') return
          yield parse(payload)
        }
        boundary = buffer.indexOf('\n\n')
      }
    }
    // 流结束但没有 [DONE] —— 把剩下的当作最后一帧
    for (const payload of extractData(buffer)) {
      if (payload === '[DONE]') return
      yield parse(payload)
    }
  } finally {
    reader.releaseLock()
  }
}
