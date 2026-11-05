/**
 * 状态行的纯函数：阶段文案、耗时格式化、token 估算。
 *
 * 抽出来是为了能单测 —— 组件里带 JSX 与计时器，Node 跑不了
 * （`npm test` 跑的是 `scripts/*.mjs`，直接跑 `.ts` 纯函数）。
 */
/**
 * 这一轮现在在干什么。
 *
 * **没有"等待审批"这一档** —— 审批卡片自己就写着"需要你批准"，状态行再说一遍
 * 是重复。等批的时候这一轮已经结束（后端发完 `pending_approval` 就收尾），
 * 状态行跟着消失；用户批完、图恢复，状态行重新出现，那时才是"思考中"。
 */
export type TurnPhase = 'thinking' | 'writing' | 'tool'

/**
 * 阶段 → 人话。
 *
 * **工具阶段只说工具名，不说参数** —— 目标（路径、命令）在上面的工具卡片里
 * 已经写着，这里再说一遍就是重复，而且 `run_python` 的参数是代码，
 * 截断了更难看。
 *
 * 传进来的是**已经翻过的人话**（`toolLabel` 的结果），不是原始 key ——
 * 这个模块不引任何东西，Node 能直接跑它（`npm test`）。
 */
export function phaseLabel(phase: TurnPhase, toolLabelText?: string | null): string {
  if (phase === 'tool' && toolLabelText) return `正在${toolLabelText}`
  if (phase === 'writing') return '正在回答'
  return '思考中'
}

/** 毫秒 → `7s` / `1m 12s`。 */
export function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}m ${rest}s`
}

/**
 * 字符数 → token 估算。
 *
 * **口径跟后端一致**（`app/agents/status.py::estimate_tokens`，一个 token
 * 折算 4 个字符）—— 两边算法不一样的话，状态行上的数字会跟右侧状态面板
 * 里对不上。
 */
export function estimateTokens(text: string): number {
  if (!text) return 0
  return Math.floor(text.length / 4)
}

/** token 数 → `275` / `1.2k`。 */
export function formatTokens(tokens: number): string {
  if (tokens < 1000) return String(tokens)
  return `${(tokens / 1000).toFixed(1)}k`
}
