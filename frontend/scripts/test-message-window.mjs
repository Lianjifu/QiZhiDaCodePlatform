/**
 * 长会话的渲染窗口 —— 纯函数，直接在 node 里跑。
 *
 * ```bash
 * npm run test
 * ```
 */
import assert from 'node:assert/strict'

import {
  DEFAULT_VISIBLE,
  PAGE_SIZE,
  messageWindow,
} from '../src/features/agent/messageWindow.ts'

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('消息不多就全渲染', () => {
  const items = [1, 2, 3]
  const window = messageWindow(items, DEFAULT_VISIBLE)
  assert.deepEqual(window.shown, [1, 2, 3])
  assert.equal(window.hidden, 0)
})

check('消息多了只渲染最近的一段', () => {
  const items = Array.from({ length: 100 }, (_, index) => index)
  const window = messageWindow(items, 40)
  assert.equal(window.shown.length, 40)
  assert.equal(window.hidden, 60)
  // 留下的是**最近**的
  assert.equal(window.shown[0], 60)
  assert.equal(window.shown[39], 99)
})

check('往上加载一批就是多渲染一些', () => {
  const items = Array.from({ length: 100 }, (_, index) => index)
  const before = messageWindow(items, 40)
  const after = messageWindow(items, 40 + PAGE_SIZE)
  assert.equal(before.hidden - after.hidden, PAGE_SIZE)
  assert.equal(after.shown.length, before.shown.length + PAGE_SIZE)
})

check('窗口比消息还大就全渲染', () => {
  const items = [1, 2]
  const window = messageWindow(items, 100)
  assert.deepEqual(window.shown, [1, 2])
  assert.equal(window.hidden, 0)
})

check('空会话不炸', () => {
  const window = messageWindow([], 40)
  assert.deepEqual(window.shown, [])
  assert.equal(window.hidden, 0)
})

check('负的窗口数量按 0 算', () => {
  const window = messageWindow([1, 2, 3], -5)
  assert.deepEqual(window.shown, [])
  assert.equal(window.hidden, 3)
})

console.log(`\n${passed} 条通过`)
