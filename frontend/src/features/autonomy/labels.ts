/**
 * 复盘记录的状态与载体 → 人话。
 *
 * 抽成纯函数是为了能单测 —— 组件里带 JSX，Node 跑不了
 * （`npm test` 跑的是 `scripts/*.mjs`，直接跑 `.ts` 纯函数）。
 */

/** 状态标签的色调（对应 `Badge` 的 tone）。 */
export type Tone = 'neutral' | 'accent' | 'success' | 'danger' | 'warning'

const STATUS_LABELS: Record<string, string> = {
  running: '复盘中',
  applied: '已生效',
  no_insight: '没有可改进的',
  rejected: '没落地',
  reverted: '已撤销',
  failed: '复盘失败',
}

const STATUS_TONES: Record<string, Tone> = {
  running: 'accent',
  applied: 'success',
  no_insight: 'neutral',
  rejected: 'warning',
  reverted: 'neutral',
  failed: 'danger',
}

const CARRIER_LABELS: Record<string, string> = {
  memory: '记忆',
  skill: '技能',
  prompt: '提示词',
}

const REJECT_REASON_LABELS: Record<string, string> = {
  safety_scope: '越界（想改权限与审批）',
  conflict: '与已有记忆矛盾',
  limit: '载体装不下了',
  target_missing: '目标没了',
  parse_failed: '模型没给出可解析的结果',
}

/** 复盘状态 → 人话；不认识就原样返回（别显示成空白）。 */
export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

/** 复盘状态 → 色调。 */
export function statusTone(status: string): Tone {
  return STATUS_TONES[status] ?? 'neutral'
}

/** 改进落在哪一层 → 人话。 */
export function carrierLabel(carrier: string): string {
  return CARRIER_LABELS[carrier] ?? carrier
}

/** 被拒的原因 → 人话。 */
export function rejectReasonLabel(reason: string): string {
  return REJECT_REASON_LABELS[reason] ?? reason
}

/**
 * 复发率 → 一句话。
 *
 * **没改之前失败过 0 次的话算不出来** —— 没有基线可比，
 * 这时返回 null 而不是 0（0 会被读成"改进后一次都没失败"，那是另一回事）。
 */
export function recurrenceText(
  before: number,
  after: number,
  rate: number | null | undefined,
): string | null {
  if (!before) return null
  if (rate === null || rate === undefined) return `${before} → ${after}`
  if (rate === 0) return `${before} → 0（没再犯）`
  if (rate >= 1) return `${before} → ${after}（没改善）`
  return `${before} → ${after}（改善 ${Math.round((1 - rate) * 100)}%）`
}
