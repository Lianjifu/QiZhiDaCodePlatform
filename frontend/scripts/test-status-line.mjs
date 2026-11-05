import assert from 'node:assert/strict'

import {
  estimateTokens,
  formatElapsed,
  formatTokens,
  phaseLabel,
} from '../src/features/agent/turnStatus.ts'

let passed = 0
function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('阶段有中文', () => {
  assert.equal(phaseLabel('thinking'), '思考中')
  assert.equal(phaseLabel('writing'), '正在回答')
})

check('工具阶段说的是人话', () => {
  // 传进来的是**已经翻过**的名字（组件里调 toolLabel 翻的）
  assert.equal(phaseLabel('tool', '运行 Python'), '正在运行 Python')
  assert.equal(phaseLabel('tool', '读取文件'), '正在读取文件')
})

check('工具阶段没给名字就退回思考中', () => {
  assert.equal(phaseLabel('tool', null), '思考中')
  assert.equal(phaseLabel('tool', ''), '思考中')
})

check('没翻过的名字原样留着，不显示成空白', () => {
  assert.equal(phaseLabel('tool', 'brand_new_tool'), '正在brand_new_tool')
})

check('耗时：秒', () => {
  assert.equal(formatElapsed(0), '0s')
  assert.equal(formatElapsed(7000), '7s')
  assert.equal(formatElapsed(59999), '59s')
})

check('耗时：过了分钟换写法', () => {
  assert.equal(formatElapsed(60000), '1m 0s')
  assert.equal(formatElapsed(72000), '1m 12s')
})

check('耗时不会显示成负数', () => {
  assert.equal(formatElapsed(-500), '0s')
})

check('token 估算：跟后端一个口径（4 字符 1 token）', () => {
  assert.equal(estimateTokens(''), 0)
  assert.equal(estimateTokens('abcd'), 1)
  assert.equal(estimateTokens('a'.repeat(1100)), 275)
})

check('token 数：一千以内不缩写', () => {
  assert.equal(formatTokens(0), '0')
  assert.equal(formatTokens(275), '275')
  assert.equal(formatTokens(999), '999')
})

check('token 数：过千用 k', () => {
  assert.equal(formatTokens(1000), '1.0k')
  assert.equal(formatTokens(1200), '1.2k')
  assert.equal(formatTokens(12500), '12.5k')
})

console.log(`\n${passed} 条通过`)
