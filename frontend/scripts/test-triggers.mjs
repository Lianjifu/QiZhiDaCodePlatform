/**
 * 触发符规则的测试 —— 纯函数，直接在 node 里跑。
 *
 * Node 24 能直接跑 .ts（类型擦除），所以这里不用构建步骤：
 *
 * ```bash
 * npm run test
 * ```
 */
import assert from 'node:assert/strict'

import { detectTrigger, mentionedPaths, orderEntries } from '../src/features/agent/triggers.ts'

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

console.log('detectTrigger')

check('行首的斜杠触发命令面板', () => {
  const result = detectTrigger('/', 1)
  assert.equal(result.kind, 'slash')
  assert.equal(result.query, '')
  assert.equal(result.start, 0)
})

check('空白后的斜杠触发命令面板，并带上过滤词', () => {
  const text = '看看 /fi'
  const result = detectTrigger(text, text.length)
  assert.equal(result.kind, 'slash')
  assert.equal(result.query, 'fi')
  assert.equal(result.start, 3)
})

check('路径里的斜杠不触发', () => {
  const text = '读 /uploads/a.txt'
  assert.equal(detectTrigger(text, text.length), null)
})

check('词中间的斜杠不触发', () => {
  const text = 'a/b'
  assert.equal(detectTrigger(text, text.length), null)
})

check('中文紧跟的斜杠不触发', () => {
  const text = '路径/x'
  assert.equal(detectTrigger(text, text.length), null)
})

check('行首的 @ 触发提及面板', () => {
  const result = detectTrigger('@', 1)
  assert.equal(result.kind, 'mention')
  assert.equal(result.query, '')
})

check('@ 后面可以直接跟路径', () => {
  const text = '@/uploads/a.png'
  const result = detectTrigger(text, text.length)
  assert.equal(result.kind, 'mention')
  assert.equal(result.query, '/uploads/a.png')
  assert.equal(result.start, 0)
})

check('@ 路径中间的斜杠不会变成命令面板', () => {
  const text = '看下 @/up'
  const result = detectTrigger(text, text.length)
  assert.equal(result.kind, 'mention')
  assert.equal(result.query, '/up')
  assert.equal(result.start, 3)
})

check('词中间的 @ 不触发', () => {
  const text = 'a@b'
  assert.equal(detectTrigger(text, text.length), null)
})

check('触发符与光标之间有空格就不触发', () => {
  const text = '/fi x'
  assert.equal(detectTrigger(text, text.length), null)
})

check('空正文不触发', () => {
  assert.equal(detectTrigger('', 0), null)
})

check('已选完的 @ 路径（后面有空格）不再触发', () => {
  const text = '@/uploads/a.png '
  assert.equal(detectTrigger(text, text.length), null)
})

console.log('mentionedPaths')

check('提取正文里的 @ 路径', () => {
  assert.deepEqual(mentionedPaths('看 @/uploads/a.png 和 @/b.txt'), [
    '/uploads/a.png',
    '/b.txt',
  ])
})

check('去重', () => {
  assert.deepEqual(mentionedPaths('@/a.png @/a.png'), ['/a.png'])
})

check('没有 @ 就是空的', () => {
  assert.deepEqual(mentionedPaths('普通文字'), [])
})

console.log('orderEntries')

check('文件排在目录前面', () => {
  const entries = [
    { path: '/uploads', is_dir: true },
    { path: '/uploads/a.png', is_dir: false },
    { path: '/b.txt', is_dir: false },
  ]
  assert.deepEqual(
    orderEntries(entries).map((item) => item.path),
    ['/uploads/a.png', '/b.txt', '/uploads'],
  )
})

check('空列表也没问题', () => {
  assert.deepEqual(orderEntries([]), [])
})

console.log(`\n${passed} 条通过`)
