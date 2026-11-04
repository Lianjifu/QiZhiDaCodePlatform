/**
 * 文本增量攒批 —— 流式输出时别每帧都重渲染。
 *
 * 模型吐字很快（一个 token 一帧）。每帧都 setState 一次，就是每帧都重渲染
 * 一遍整条消息，Markdown 也要重解析一次 —— 长回答能白烧掉好几秒主线程。
 *
 * 攒到下一帧再一起上：人眼看不出差别，渲染次数少一个数量级。
 */

/** 调度器 —— 默认用 requestAnimationFrame，测试里换成假的。 */
export interface Scheduler {
  schedule(callback: () => void): number
  cancel(handle: number): void
}

/**
 * 浏览器里的调度器：一帧一次。
 *
 * **没有 requestAnimationFrame 就退化成 setTimeout** —— 后台标签页里 rAF
 * 不会触发（那段正好不用渲染），但测试环境与老浏览器里可能压根没有这个 API。
 */
export const frameScheduler: Scheduler = {
  schedule: (callback) => {
    if (typeof requestAnimationFrame === 'function') {
      return requestAnimationFrame(callback)
    }
    return window.setTimeout(callback, 16)
  },
  cancel: (handle) => {
    if (typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(handle)
      return
    }
    window.clearTimeout(handle)
  },
}

export class TextBatcher {
  /** 还没刷出去的字 */
  private pending = ''
  /** 已排队的那个定时器 */
  private handle: number | null = null
  /** 刷出去的时候叫谁 */
  private readonly onFlush: (text: string) => void
  /** 什么时候算"到点" */
  private readonly scheduler: Scheduler

  constructor(
    onFlush: (text: string) => void,
    scheduler: Scheduler = frameScheduler,
  ) {
    this.onFlush = onFlush
    this.scheduler = scheduler
  }

  /** 攒一段文字；到点了就一起刷出去。 */
  push(text: string): void {
    if (!text) return
    this.pending += text
    if (this.handle !== null) return
    this.handle = this.scheduler.schedule(() => {
      this.handle = null
      this.flush()
    })
  }

  /**
   * 立刻把攒着的刷出去。
   *
   * 流结束、用户叫停时用 —— 不刷的话最后几个字要等下一帧才出现，
   * 甚至在叫停时就永远留在了屏幕上。
   */
  flush(): void {
    if (this.handle !== null) {
      this.scheduler.cancel(this.handle)
      this.handle = null
    }
    if (!this.pending) return
    const text = this.pending
    this.pending = ''
    this.onFlush(text)
  }

  /** 还有没有没刷出去的字（测试与调试用）。 */
  get pendingText(): string {
    return this.pending
  }
}
