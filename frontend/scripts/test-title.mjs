/**
 * 会话标题的测试 —— 纯函数，直接在 node 里跑。
 *
 * 跟触发符一样用 Node 24 的类型擦除（`npm run test`）。
 */
import assert from 'node:assert/strict'

import { titleFromMessage } from '../src/features/sessions/title.ts'

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('普通句子', () => {
  assert.equal(titleFromMessage('写一个 c.txt'), '写一个 c.txt')
})

check('去掉标题记号', () => {
  assert.equal(titleFromMessage('# 一级标题'), '一级标题')
  assert.equal(titleFromMessage('### 三级标题'), '三级标题')
})

check('去掉强调记号', () => {
  assert.equal(titleFromMessage('**加粗**的标题'), '加粗的标题')
  assert.equal(titleFromMessage('`代码`的标题'), '代码的标题')
})

check('去掉引用与列表记号', () => {
  assert.equal(titleFromMessage('> 引用的标题'), '引用的标题')
  assert.equal(titleFromMessage('- 列表的标题'), '列表的标题')
})

check('只取第一行', () => {
  assert.equal(titleFromMessage('第一行\n第二行'), '第一行')
})

check('空输入给默认标题', () => {
  assert.equal(titleFromMessage(''), '新会话')
  assert.equal(titleFromMessage('   \n  '), '新会话')
  assert.equal(titleFromMessage('***'), '新会话')
})

check('超长截断到 30 个字', () => {
  assert.equal(titleFromMessage('啊'.repeat(50)).length, 30)
})

console.log(`\n${passed} 条通过`)
