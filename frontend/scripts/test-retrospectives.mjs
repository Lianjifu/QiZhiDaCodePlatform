import assert from 'node:assert/strict'

import {
  carrierLabel,
  recurrenceText,
  rejectReasonLabel,
  statusLabel,
  statusTone,
} from '../src/features/autonomy/labels.ts'

let passed = 0
function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('复盘状态有中文', () => {
  assert.equal(statusLabel('applied'), '已生效')
  assert.equal(statusLabel('reverted'), '已撤销')
  assert.equal(statusLabel('rejected'), '没落地')
})

check('不认识的状态原样返回', () => {
  assert.equal(statusLabel('whatever'), 'whatever')
  assert.equal(statusTone('whatever'), 'neutral')
})

check('已生效是绿的，失败是红的', () => {
  assert.equal(statusTone('applied'), 'success')
  assert.equal(statusTone('failed'), 'danger')
  assert.equal(statusTone('rejected'), 'warning')
})

check('载体有中文', () => {
  assert.equal(carrierLabel('memory'), '记忆')
  assert.equal(carrierLabel('skill'), '技能')
  assert.equal(carrierLabel('prompt'), '提示词')
})

check('被拒原因说清是哪一条', () => {
  assert.ok(rejectReasonLabel('safety_scope').includes('越界'))
  assert.ok(rejectReasonLabel('conflict').includes('矛盾'))
})

check('复发率：没基线就算不出来', () => {
  // 改进之前 0 次失败 —— 没有基线，返回 null 而不是 0
  assert.equal(recurrenceText(0, 0, null), null)
  assert.equal(recurrenceText(0, 3, null), null)
})

check('复发率：没再犯', () => {
  assert.equal(recurrenceText(4, 0, 0), '4 → 0（没再犯）')
})

check('复发率：照旧没改善', () => {
  assert.equal(recurrenceText(3, 3, 1), '3 → 3（没改善）')
})

check('复发率：改善了多少', () => {
  assert.equal(recurrenceText(4, 1, 0.25), '4 → 1（改善 75%）')
})

check('复发率：没给比率就只报两个数', () => {
  assert.equal(recurrenceText(4, 2, null), '4 → 2')
})

console.log(`\n${passed} 条通过`)
