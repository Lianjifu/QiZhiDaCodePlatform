/**
 * 命令面板过滤的测试 —— 纯函数，直接在 node 里跑。
 *
 * Node 24 能直接跑 .ts（类型擦除），所以这里不用构建步骤：
 *
 * ```bash
 * npm run test
 * ```
 */
import assert from 'node:assert/strict'

import { filterCommands } from '../src/features/commands/filter.ts'

const COMMANDS = [
  { id: 'new', title: '新建会话', hint: '开一个空白对话' },
  { id: 'files', title: '工作区文件', hint: '文件与上传件' },
  { id: 'cap', title: '能力清单', hint: '工具 / 技能 / MCP', keywords: '工具 技能' },
  { id: 'theme', title: '切换到深色', hint: '主题' },
]

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('空搜索词返回全部', () => {
  assert.equal(filterCommands(COMMANDS, '').length, 4)
  assert.equal(filterCommands(COMMANDS, '   ').length, 4)
})

check('按标题匹配', () => {
  const result = filterCommands(COMMANDS, '文件')
  assert.equal(result.length, 1)
  assert.equal(result[0].id, 'files')
})

check('按副标题匹配', () => {
  const result = filterCommands(COMMANDS, '主题')
  assert.deepEqual(
    result.map((item) => item.id),
    ['theme'],
  )
})

check('按关键词匹配', () => {
  const result = filterCommands(COMMANDS, '技能')
  assert.equal(result[0].id, 'cap')
})

check('大小写不敏感', () => {
  assert.equal(filterCommands(COMMANDS, 'MCP').length, 1)
  assert.equal(filterCommands(COMMANDS, 'mcp').length, 1)
})

check('没有匹配就是空', () => {
  assert.deepEqual(filterCommands(COMMANDS, '不存在的东西'), [])
})

console.log(`\n${passed} 条通过`)
