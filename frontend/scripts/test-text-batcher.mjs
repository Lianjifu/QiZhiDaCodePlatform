/**
 * 文本增量的攒批 —— 纯逻辑，直接在 node 里跑。
 *
 * ```bash
 * npm run test
 * ```
 */
import assert from 'node:assert/strict'

import { TextBatcher } from '../src/features/agent/textBatcher.ts'

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

/** 假调度器 —— 手动决定什么时候"到点"。 */
function fakeScheduler() {
  const queue = []
  const scheduler = {
    schedule(callback) {
      queue.push(callback)
      return queue.length
    },
    cancel(handle) {
      queue[handle - 1] = null
    },
  }
  return {
    scheduler,
    /** 跑掉所有排队的回调（模拟"下一帧到了"）。 */
    tick() {
      const current = queue.splice(0, queue.length)
      for (const callback of current) {
        if (callback) callback()
      }
    },
  }
}

check('多段文字合成一次刷出', () => {
  const { scheduler, tick } = fakeScheduler()
  const flushed = []
  const batcher = new TextBatcher((text) => flushed.push(text), scheduler)
  batcher.push('你')
  batcher.push('好')
  batcher.push('！')
  assert.deepEqual(flushed, [], '还没到点，不该刷')
  tick()
  assert.deepEqual(flushed, ['你好！'])
})

check('空字符串不进队列', () => {
  const { scheduler, tick } = fakeScheduler()
  const flushed = []
  const batcher = new TextBatcher((text) => flushed.push(text), scheduler)
  batcher.push('')
  tick()
  assert.deepEqual(flushed, [])
})

check('立刻刷会把攒着的都吐出来', () => {
  const { scheduler } = fakeScheduler()
  const flushed = []
  const batcher = new TextBatcher((text) => flushed.push(text), scheduler)
  batcher.push('abc')
  batcher.flush()
  assert.deepEqual(flushed, ['abc'])
  assert.equal(batcher.pendingText, '')
})

check('刷过之后定时器不重复触发', () => {
  const { scheduler, tick } = fakeScheduler()
  const flushed = []
  const batcher = new TextBatcher((text) => flushed.push(text), scheduler)
  batcher.push('a')
  batcher.flush()
  tick()
  assert.deepEqual(flushed, ['a'], '刷过之后不该再刷一次')
})

check('连续几轮：每轮都刷得干净', () => {
  const { scheduler, tick } = fakeScheduler()
  const flushed = []
  const batcher = new TextBatcher((text) => flushed.push(text), scheduler)
  batcher.push('第一轮')
  tick()
  batcher.push('第二轮')
  tick()
  assert.deepEqual(flushed, ['第一轮', '第二轮'])
})

check('空的时候刷不触发回调', () => {
  const { scheduler } = fakeScheduler()
  let count = 0
  const batcher = new TextBatcher(() => (count += 1), scheduler)
  batcher.flush()
  assert.equal(count, 0)
})

console.log(`\n${passed} 条通过`)
