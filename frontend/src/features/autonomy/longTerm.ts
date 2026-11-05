/**
 * 长期记忆状态的显示文案。
 *
 * 单独放一个模块是为了能单独测 —— 这几句文案是"人能不能一眼看懂自己的
 * agent 记住了什么"的一部分，改坏了看不出来。
 */
import type { LongTermStatus } from '../../types/api'

/** 就绪 / 没就绪。 */
export function longTermReadyLabel(status: LongTermStatus): string {
  return status.ready ? '已就绪' : '未初始化'
}

/**
 * 状态详情。
 *
 * 没就绪时给一句可操作的话（后端的 `hint`）—— 光说"没初始化"没用，
 * 人家不知道怎么修。
 */
export function longTermDetail(status: LongTermStatus): string {
  if (!status.ready) return status.hint
  const parts = [`集合 ${status.collection_name} 已经建好，里面有 ${status.doc_count} 条记忆。`]
  if (status.keyword_only) {
    parts.push('只建了关键词索引 —— 召回能用，语义检索用不上。')
  } else {
    parts.push(`向量化模型 ${status.embedding_model}（${status.embedding_dim} 维）。`)
  }
  return parts.join(' ')
}
