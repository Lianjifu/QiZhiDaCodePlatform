/**
 * 能力清单里的风险显示规则。
 *
 * 审批按**风险档位**判（后端 `app/capabilities/registration/policy.py`）：
 *
 * - 低（只读、写入）→ 自动放行；
 * - 中（执行命令、联网）→ 要人批；
 * - 高（破坏性）→ 永远要人批。
 *
 * 这里只管**怎么给人看** —— 判定在后端，前端不自己再判一遍。
 */

/** 风险档位。 */
export type RiskTier = 'low' | 'medium' | 'high'

/** 风险等级 → 档位。与后端的 `risk_tier` 一致。 */
const RISK_TIERS: Record<string, RiskTier> = {
  read: 'low',
  write: 'low',
  execute: 'medium',
  network: 'medium',
  destructive: 'high',
}

/** 风险等级 → 人话。 */
const RISK_LABELS: Record<string, string> = {
  read: '只读',
  write: '写入',
  execute: '执行命令',
  network: '联网',
  destructive: '破坏性',
}

/** 档位 → 人话。 */
const TIER_LABELS: Record<RiskTier, string> = {
  low: '低',
  medium: '中',
  high: '高',
}

/** 风险等级 → 档位；不认识的一律按低档（后端也是这么兜底的）。 */
export function riskTier(risk: string): RiskTier {
  return RISK_TIERS[risk] ?? 'low'
}

/** 风险等级 → 人话。 */
export function riskLabel(risk: string): string {
  return RISK_LABELS[risk] ?? risk
}

/** 档位 → 人话（低 / 中 / 高）。 */
export function tierLabel(tier: RiskTier): string {
  return TIER_LABELS[tier]
}

/**
 * 这个能力要不要人批 —— 只按**档位**算，够用来在界面上说明。
 *
 * 真正的判定还有两个后端口子（``permission=admin`` 永远要批、
 * ``permission=confirm`` 显式要求人批），这两个前端不猜。
 */
export function needsApprovalByTier(risk: string): boolean {
  return riskTier(risk) !== 'low'
}

/**
 * 表格里那一列：档位 + 要不要人批。
 *
 * 例：`中 · 要人批` / `低 · 自动放行`。
 */
export function approvalSummary(risk: string): string {
  const tier = riskTier(risk)
  return `${TIER_LABELS[tier]} · ${needsApprovalByTier(risk) ? '要人批' : '自动放行'}`
}
