/**
 * 会话状态的共享仓库 —— 纯逻辑，直接在 node 里跑。
 *
 * ```bash
 * npm run test
 * ```
 */
import assert from 'node:assert/strict'

import { createStatusStore } from '../src/features/sessions/statusStore.ts'

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

/** 一个假的会话状态。 */
function fakeStatus(threadId) {
  return { thread_id: threadId, context: { percent: 10 } }
}

/** 等微任务队列排空。 */
function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

check('拉一次就有数据了', async () => {
  const store = createStatusStore(async (id) => fakeStatus(id))
  assert.equal(store.get('t1').status, null)
  await store.refresh('t1')
  assert.equal(store.get('t1').status.thread_id, 't1')
  store.dispose()
})

check('同时要两次只发一个请求', async () => {
  let calls = 0
  const store = createStatusStore(async (id) => {
    calls += 1
    await flush()
    return fakeStatus(id)
  })
  await Promise.all([store.refresh('t1'), store.refresh('t1')])
  assert.equal(calls, 1, '两个地方同时要，不该发两个请求')
  store.dispose()
})

check('两个订阅者共用一份数据', async () => {
  let calls = 0
  const store = createStatusStore(async (id) => {
    calls += 1
    return fakeStatus(id)
  })
  const seen = []
  store.subscribe('t1', () => seen.push('a'))
  store.subscribe('t1', () => seen.push('b'))
  await store.refresh('t1')
  assert.deepEqual(seen.sort(), ['a', 'b'], '两个订阅者都要被通知')
  assert.equal(calls, 1)
  store.dispose()
})

check('换会话读到的是新会话的数据', async () => {
  const store = createStatusStore(async (id) => fakeStatus(id))
  await store.refresh('t1')
  await store.refresh('t2')
  assert.equal(store.get('t1').status.thread_id, 't1')
  assert.equal(store.get('t2').status.thread_id, 't2')
  store.dispose()
})

check('读失败保留上一次的数据', async () => {
  let fail = false
  const store = createStatusStore(async (id) => {
    if (fail) throw new Error('炸了')
    return fakeStatus(id)
  })
  await store.refresh('t1')
  fail = true
  await store.refresh('t1')
  assert.equal(store.get('t1').status.thread_id, 't1', '读失败不该把数据清空')
  assert.equal(store.get('t1').error, '炸了')
  store.dispose()
})

check('退订之后不再通知', async () => {
  const store = createStatusStore(async (id) => fakeStatus(id))
  let count = 0
  const off = store.subscribe('t1', () => {
    count += 1
  })
  await store.refresh('t1')
  const before = count
  off()
  await store.refresh('t1')
  assert.equal(count, before, '退订了就不该再收到通知')
  store.dispose()
})

await flush()
console.log(`\n${passed} 条通过`)
