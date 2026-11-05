import assert from 'node:assert/strict'

import {
  parseSections,
  serializeSections,
  toggleSection,
  SECTIONS_KEY,
} from '../src/components/layout/sidebarSections.ts'

let passed = 0
function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('存的键跟侧栏折叠不是一个键', () => {
  // 两件事：一件是整条侧栏收起，一件是分区折起 —— 别互相覆盖
  assert.notEqual(SECTIONS_KEY, 'qzd.sidebar.collapsed')
})

check('没存过就是全展开', () => {
  assert.deepEqual(parseSections(null), {})
  assert.deepEqual(parseSections(''), {})
})

check('读回自己写进去的', () => {
  const state = { workspace: true, sessions: false }
  assert.deepEqual(parseSections(serializeSections(state)), state)
})

check('存坏了不当回事 —— 侧栏少显示几行不该白屏', () => {
  assert.deepEqual(parseSections('{不是 JSON'), {})
  assert.deepEqual(parseSections('[1,2,3]'), {})
  assert.deepEqual(parseSections('null'), {})
  assert.deepEqual(parseSections('"字符串"'), {})
})

check('只认布尔值', () => {
  assert.deepEqual(parseSections('{"workspace":"是"}'), {})
  assert.deepEqual(parseSections('{"workspace":1}'), {})
  assert.deepEqual(parseSections('{"workspace":true}'), { workspace: true })
})

check('折起再展开会回去', () => {
  let state = {}
  state = toggleSection(state, 'sessions')
  assert.equal(state.sessions, true)
  state = toggleSection(state, 'sessions')
  assert.equal(state.sessions, false)
})

check('折一个分区不影响别的', () => {
  const state = toggleSection({ sessions: true }, 'workspace')
  assert.equal(state.sessions, true)
  assert.equal(state.workspace, true)
})

check('翻折叠不改原来的对象', () => {
  const before = { sessions: true }
  const after = toggleSection(before, 'workspace')
  assert.equal(before.workspace, undefined)
  assert.equal(after.workspace, true)
})

console.log(`\n${passed} 条通过`)
