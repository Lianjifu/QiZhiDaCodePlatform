/**
 * 工具调用摘要规则的测试 —— 纯函数，直接在 node 里跑。
 *
 * ```bash
 * npm run test
 * ```
 */
import assert from 'node:assert/strict'

import {
  isFailedResult,
  toolArgRows,
  toolLabel,
  toolTarget,
} from '../src/features/agent/toolSummary.ts'

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('工具名翻成人话', () => {
  assert.equal(toolLabel('read_file'), '读取文件')
  assert.equal(toolLabel('git_status'), '查看 git 状态')
  assert.equal(toolLabel('delegate_worker'), '派子 agent')
  // 不认识的原样返回 —— 别显示成空白
  assert.equal(toolLabel('some_new_tool'), 'some_new_tool')
})

check('挑出最能说明问题的那项参数', () => {
  assert.equal(toolTarget({ file_path: '/README.md' }), '/README.md')
  assert.equal(toolTarget({ path: '/backend' }), '/backend')
  assert.equal(toolTarget({ pattern: '*.py' }), '*.py')
  assert.equal(toolTarget({ branch: 'feat/x' }), 'feat/x')
  assert.equal(toolTarget({ command: 'ls -la' }), 'ls -la')
  // 代码只取第一行
  assert.equal(toolTarget({ code: 'print(1)\nprint(2)' }), 'print(1)')
  // 没有就空
  assert.equal(toolTarget({}), '')
})

check('参数逐行列出来', () => {
  const rows = toolArgRows({ path: '/backend', limit: 20, ok: true })
  assert.equal(rows.length, 3)
  assert.deepEqual(rows[0], { key: 'path', value: '/backend', structured: false })
  assert.deepEqual(rows[1], { key: 'limit', value: '20', structured: false })
  assert.deepEqual(rows[2], { key: 'ok', value: 'true', structured: false })
})

check('对象与数组标成结构化', () => {
  const rows = toolArgRows({ args: { a: 1 }, list: [1, 2], nothing: null })
  assert.equal(rows[0].structured, true)
  assert.equal(rows[0].value, '{"a":1}')
  assert.equal(rows[1].structured, true)
  assert.equal(rows[2].value, '')
})

check('认得出失败的工具结果', () => {
  // deepAgents 失败时把错误写进结果里，不抛异常
  assert.equal(isFailedResult("Error: File '/x.md' not found"), true)
  assert.equal(isFailedResult('  Error: Path not found'), true)
  assert.equal(isFailedResult('文件读到了内容'), false)
  assert.equal(isFailedResult(''), false)
  assert.equal(isFailedResult(null), false)
})

console.log(`\n${passed} 条通过`)