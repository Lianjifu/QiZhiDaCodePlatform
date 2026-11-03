/**
 * 工作目录显示规则的测试 —— 纯函数，直接在 node 里跑。
 *
 * ```bash
 * npm run test
 * ```
 */
import assert from 'node:assert/strict'

import {
  bindableEnvironments,
  isWorkerBranch,
  kindLabel,
} from '../src/features/environments/format.ts'

const ENVIRONMENTS = [
  { path: '/Users/me/ops', name: 'ops' },
  { path: '/Users/me/ops/.qzd-worktrees', name: '.qzd-worktrees' },
  { path: '/Users/me/ops/demo', name: 'demo' },
  { path: '/Users/me/ops/.git', name: '.git' },
]

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('类型的中文名', () => {
  assert.equal(kindLabel('worktree'), 'Worktree')
  assert.equal(kindLabel('repo'), '仓库')
  assert.equal(kindLabel('folder'), '目录')
  assert.equal(kindLabel('别的'), '目录')
  // 仓库是靠 is_repo 认出来的 —— kind 只有 folder / worktree 两种
  assert.equal(kindLabel('folder', true), '仓库')
  assert.equal(kindLabel('worktree', true), 'Worktree')
})

check('隐藏目录不能绑成工作目录', () => {
  const result = bindableEnvironments(ENVIRONMENTS)
  assert.deepEqual(
    result.map((item) => item.name),
    ['ops', 'demo'],
  )
})

check('没有名字就用路径的最后一段', () => {
  const result = bindableEnvironments([{ path: '/a/b/xyz', name: '' }])
  assert.equal(result.length, 1)
})

check('认得出平台派出去的分支', () => {
  assert.equal(isWorkerBranch('qzd/feat-x-1a2b'), true)
  assert.equal(isWorkerBranch('main'), false)
  assert.equal(isWorkerBranch('feature/qzd'), false)
})

console.log(`\n${passed} 条通过`)
