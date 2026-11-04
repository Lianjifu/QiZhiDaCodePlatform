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

check('读文件带出行范围', () => {
  // 同一份文件分段读时，光看路径分不出是在往下读
  assert.equal(toolTarget({ file_path: '/README.md' }), '/README.md')
  assert.equal(
    toolTarget({ file_path: '/README.md', offset: 100, limit: 220 }),
    '/README.md（101-320 行）',
  )
  assert.equal(
    toolTarget({ file_path: '/README.md', limit: 150 }),
    '/README.md（1-150 行）',
  )
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

check('命令失败认得出来', () => {
  // execute 不抛异常也不带退出码 —— 失败只在 stderr 里
  assert.equal(isFailedResult("/workspace/a.sh: 1: cd: can't cd to /x", 'execute'), true)
  assert.equal(isFailedResult('sh: git: command not found', 'execute'), true)
  assert.equal(isFailedResult('Permission denied', 'execute'), true)
  assert.equal(isFailedResult('一切正常\n第二行', 'execute'), false)
  // 读文件的内容里出现 "not found" 是正常的 —— 不能算失败
  assert.equal(isFailedResult('No such file or directory', 'read_file'), false)
})

console.log(`\n${passed} 条通过`)