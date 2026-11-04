/**
 * 权限模式的清单与显示规则 —— 纯函数，直接在 node 里跑。
 *
 * ```bash
 * npm run test
 * ```
 */
import assert from 'node:assert/strict'

import {
  PERMISSION_MODES,
  permissionLabel,
} from '../src/features/agent/permissionModes.ts'

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('五个模式，顺序就是菜单顺序', () => {
  assert.deepEqual(
    PERMISSION_MODES.map((item) => item.key),
    ['auto', 'manual', 'accept_edits', 'plan', 'bypass'],
  )
  assert.equal(PERMISSION_MODES[0].recommended, true)
  assert.ok(PERMISSION_MODES.every((item) => item.label && item.hint))
})

check('模式名 → 中文标签', () => {
  assert.equal(permissionLabel('auto'), '自动')
  assert.equal(permissionLabel('accept_edits'), '接受编辑')
  assert.equal(permissionLabel('plan'), '只读')
  assert.equal(permissionLabel('bypass'), '绕过权限')
  // 不认识的就原样返回
  assert.equal(permissionLabel('unknown'), 'unknown')
})

console.log(`\n${passed} 条通过`)
