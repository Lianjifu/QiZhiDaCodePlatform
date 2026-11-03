/**
 * 能力清单的风险档位显示规则 —— 纯函数，直接在 node 里跑。
 *
 * ```bash
 * npm run test
 * ```
 */
import assert from 'node:assert/strict'

import {
  approvalSummary,
  needsApprovalByTier,
  riskLabel,
  riskTier,
  tierLabel,
} from '../src/features/capabilities/risk.ts'

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('风险等级归到档位', () => {
  assert.equal(riskTier('read'), 'low')
  assert.equal(riskTier('write'), 'low')
  assert.equal(riskTier('execute'), 'medium')
  assert.equal(riskTier('network'), 'medium')
  assert.equal(riskTier('destructive'), 'high')
  // 不认识的按低档 —— 与后端的兜底一致
  assert.equal(riskTier('乱写的'), 'low')
})

check('只有中高档要人批', () => {
  assert.equal(needsApprovalByTier('read'), false)
  assert.equal(needsApprovalByTier('write'), false)
  assert.equal(needsApprovalByTier('execute'), true)
  assert.equal(needsApprovalByTier('network'), true)
  assert.equal(needsApprovalByTier('destructive'), true)
})

check('给人看的字', () => {
  assert.equal(riskLabel('execute'), '执行命令')
  assert.equal(riskLabel('destructive'), '破坏性')
  assert.equal(tierLabel('medium'), '中')
  assert.equal(approvalSummary('write'), '低 · 自动放行')
  assert.equal(approvalSummary('execute'), '中 · 要人批')
  assert.equal(approvalSummary('destructive'), '高 · 要人批')
})

console.log(`\n${passed} 条通过`)
