/**
 * 状态行 —— 助手正在输出时，正文下面那一行。
 *
 * ```text
 * 🍀   7s · ~275 tokens · 思考中
 * ```
 *
 * **它说的是"这一轮正在发生什么"** —— 呼吸点、已用时、token 估算、当前阶段。
 * 工具块与审批卡片说的是"做了什么"，不重复。
 *
 * ## 为什么要有这一行
 *
 * 之前整轮只有正文最后那个一闪一闪的方块在动，界面是静默的：用户不知道
 * agent 是在想、在写、在跑工具还是在等自己批。而"思考中"的提示又只在模型
 * 吐出第一个字**之前**出现，一开口就没了。
 */
import { useEffect, useState } from 'react'

import Clover from './Clover'
import { toolLabel } from './toolSummary'
import { estimateTokens, formatElapsed, formatTokens, phaseLabel, type TurnPhase } from './turnStatus'

/** 计时器的刷新间隔（毫秒）。250ms 足够顺滑，再密只是白烧 CPU。 */
const TICK_MS = 250

export default function StatusLine({
  phase,
  startedAt,
  toolName,
  content,
  className,
}: {
  phase: TurnPhase
  /** 这一轮开始的时刻（毫秒时间戳） */
  startedAt: number
  /** 正在跑的工具名（阶段是 tool 时才有） */
  toolName?: string | null
  /** 这一轮已经输出的正文 —— 用来估 token */
  content: string
  /** 外边距交给调用方 —— 位置变了，间距要求也变了 */
  className?: string
}) {
  const elapsed = useElapsed(startedAt)
  const tokens = estimateTokens(content)
  // 工具名翻成人话（"运行 Python"）—— 不认识的名字原样留着
  const tool = toolName ? toolLabel(toolName) : null

  return (
    <div
      className={[
        'flex items-center gap-2 text-[11px] leading-none text-ink-faint',
        className ?? '',
      ].join(' ')}
    >
      <Clover className="clover h-3.5 w-3.5 shrink-0 text-clover" />
      <span className="tabular-nums">{formatElapsed(elapsed)}</span>
      <span className="opacity-40">·</span>
      <span className="tabular-nums">~{formatTokens(tokens)} tokens</span>
      <span className="opacity-40">·</span>
      {/* 阶段比旁边两项亮一档 —— 它是"现在在干什么"，其余是"跑了多少" */}
      <span className="text-ink-muted">{phaseLabel(phase, tool)}</span>
    </div>
  )
}

/**
 * 已经过去多久。
 *
 * **计时器挂在组件自己身上** —— 每 250ms 只重渲染这一行，不带着整条消息
 * （正文里的 Markdown）一起重渲染。
 */
function useElapsed(startedAt: number): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!startedAt) return
    // **不同步 setState** —— 换轮时 `now` 会短暂偏旧（最多差一个 tick），
    // 反正显示的是"已经过去多久"，差 250ms 看不出来。
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS)
    return () => window.clearInterval(timer)
  }, [startedAt])

  if (!startedAt) return 0
  return Math.max(0, now - startedAt)
}
