/**
 * 环境（工作目录）的显示规则。
 *
 * 抽成纯函数是为了能单测 —— 组件里带 JSX，Node 跑不了（`npm test` 跑的是
 * `scripts/*.mjs`，直接跑 `.ts` 纯函数）。
 */

/** 环境类型的中文名。 */
export function kindLabel(kind: string): string {
  if (kind === 'worktree') return 'Worktree'
  if (kind === 'repo') return '仓库'
  return '目录'
}

/**
 * 能绑成工作目录的环境。
 *
 * 滤掉平台的隐藏目录（``.qzd-worktrees`` 那种）—— 那是平台自己放
 * worktree 的地方，不是给人用的工作目录。
 */
export function bindableEnvironments<T extends { path: string; name: string }>(
  environments: T[],
): T[] {
  return environments.filter((item) => {
    const name = item.name || item.path.split('/').pop() || ''
    return !name.startsWith('.')
  })
}

/** 分支名里的平台标记 —— 派出去的子 agent 都用这个前缀。 */
export const WORKER_BRANCH_PREFIX = 'qzd/'

/** 这条分支是不是平台派出去的。 */
export function isWorkerBranch(branch: string): boolean {
  return branch.startsWith(WORKER_BRANCH_PREFIX)
}
