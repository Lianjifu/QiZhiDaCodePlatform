/**
 * 编码助手 —— 唯一支持多轮对话的页面。
 *
 * 空状态（还没发过消息）：输入框居中，像 Claude 桌面端那样。
 * 有消息之后：消息列表滚动，输入框固定在底部。
 *
 * **会话 ID 是多轮的钥匙** —— 后端靠它串 checkpoint，前端只要一直带着同一个
 * ``thread_id``，历史就接得上。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate, useOutletContext, useParams } from 'react-router'

import { clearConversation } from '../../api/resources'
import { useConfirm } from '../../components/ui/useConfirm'
import { usePrompt } from '../../components/ui/usePrompt'
import EnvironmentBar from '../environments/EnvironmentBar'
import Composer, { type ComposerHandle } from './Composer'
import MessageList from './MessageList'
import { DEFAULT_VISIBLE, PAGE_SIZE } from './messageWindow'
import { useChat } from './useChat'
import { newThreadId, useSessions } from '../sessions/store'
import { titleFromMessage } from '../sessions/title'

/** 空状态页的推荐问题 —— 点一下填进输入框。 */
const SUGGESTIONS = [
  '列出工作区里的文件',
  '讲讲这个项目是干嘛的',
  '当前能调用哪些能力',
  '先给一个改造方案',
]

/** 离底部多近算「还在底部」—— 差这么点就不用打扰用户了。 */
const STICK_TO_BOTTOM_SLACK = 64

export default function ChatPage() {
  const { threadId: routeThreadId } = useParams<{ threadId: string }>()
  const navigate = useNavigate()
  const { touch, rename, sessions } = useSessions()
  const chat = useChat()
  // 外壳把"打开右侧状态面板"的动作传下来（AppShell → Outlet context）
  const { openStatusPanel } = useOutletContext<{ openStatusPanel: (threadId?: string) => void }>()
  const { confirm, dialog: confirmDialog } = useConfirm()
  const { prompt, dialog: promptDialog } = usePrompt()

  // 没有 URL 里的会话就临时开一个 —— 发出第一条消息时才落到路由上
  const [draftThreadId, setDraftThreadId] = useState(() => newThreadId())
  const threadId = routeThreadId ?? draftThreadId

  const loadedRef = useRef<string>('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<ComposerHandle>(null)
  /**
   * **一次渲染多少条消息**。
   *
   * 长会话全渲染会攒上万个 DOM 节点，Markdown 解析也跟着拖慢。这里只渲染
   * 最近的一段，更早的按需加载 —— 消息没丢，只是不在 DOM 里。
   */
  const [visibleCount, setVisibleCount] = useState(DEFAULT_VISIBLE)
  /**
   * 加载更早消息**之前**的内容高度。
   *
   * 加载后上面多出一截，不补滚动位置的话，用户会看到画面突然跳到别处 ——
   * 看着像"对话被改了"。
   */
  const heightBeforeLoadRef = useRef(0)
  /**
   * **用户是不是还贴着底部看**。
   *
   * 流式时每来一帧就无条件滚到底的话，用户往上一翻就被立刻拽回来，
   * 边生成边读上文（最常见的用法）根本做不到。
   */
  const stickToBottomRef = useRef(true)
  /**
   * **正在流式的那个会话**（只有这一个）。
   *
   * 以前是个"流过的会话"的集合，永远不清 —— 结果切回一个流过的会话时，
   * 历史加载被跳过，屏幕上留着**别的会话**的对话。
   */
  const streamingThreadRef = useRef<string | null>(null)
  /** 上一次的路由会话 —— 用来发现「从某个会话退回 /chat」 */
  const prevRouteRef = useRef<string | undefined>(routeThreadId)

  // 换会话：把那个会话的历史拉回来
  useEffect(() => {
    const prev = prevRouteRef.current
    prevRouteRef.current = routeThreadId
    if (!routeThreadId) {
      chat.reset()
      loadedRef.current = ''
      // 从某个会话退回 /chat（侧栏「新建」）—— 换一个新会话 id，
      // 别把新消息接到刚才那个会话的 checkpoint 上。
      if (prev) setDraftThreadId(newThreadId())
      return
    }
    // 切走了就别让旧会话的流继续写 —— 它会把文本贴到**现在这个**会话上
    const streaming = streamingThreadRef.current === routeThreadId
    if (streamingThreadRef.current && !streaming) {
      chat.abort()
    }
    if (loadedRef.current === routeThreadId) return
    // 这个会话的消息正在屏幕上流着呢 —— 拉历史会把它冲掉
    // （待批卡片、进行中的工具块都在流里，历史里没有）。
    if (streaming) return
    loadedRef.current = routeThreadId
    // **切会话先清屏** —— 历史加载失败时不能把上一个会话的消息留在
    // 新会话的 URL 下（用户以为在看新会话，看见的却是上一段对话）。
    stickToBottomRef.current = true
    setVisibleCount(DEFAULT_VISIBLE)
    chat.reset()
    void chat.loadHistory(routeThreadId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeThreadId])

  const empty = chat.messages.length === 0

  /** 开一个新会话。 */
  const startNewSession = () => {
    setDraftThreadId(newThreadId())
    stickToBottomRef.current = true
    chat.reset()
    loadedRef.current = ''
    if (routeThreadId) navigate('/chat')
  }

  /** 斜杠命令里的本地动作。 */
  const runCommandAction = async (action: string) => {
    if (action === 'new') {
      startNewSession()
      return
    }
    if (action === 'clear') {
      const ok = await confirm({
        title: '清空这个会话？',
        description: '这个会话里的消息与上下文都会被清掉 —— 工作区里的文件不动。',
        confirmText: '清空',
        danger: true,
      })
      if (!ok) return
      try {
        await clearConversation(threadId)
      } catch (cause) {
        await confirm({
          title: '清空失败',
          description: cause instanceof Error ? cause.message : String(cause),
          confirmText: '知道了',
          cancelText: '关闭',
        })
        return
      }
      chat.reset()
      return
    }
    if (action === 'rename') {
      // **不用 window.prompt** —— 原生框的样式跟平台没关系，还会阻塞整页。
      const title = await prompt({
        title: '给这个会话起个名字',
        defaultValue: currentTitle(),
        confirmText: '改名',
      })
      if (!title) return
      // 会话可能还没进列表（刚 /new 出来的）—— 先记一笔再改名
      touch(threadId)
      rename(threadId, title)
    }
  }

  /**
   * 从某条消息分支出新会话。
   *
   * 分支出来的是**新会话** —— 有自己的 id、自己的侧栏条目、自己的工作区
   * （工作区文件也复制过去了）。
   */
  const handleFork = async (sourceId: string | null) => {
    const newThread = await chat.forkAt(threadId, sourceId)
    if (!newThread) return
    touch(newThread, `${currentTitle()}（分支）`)
    navigate(`/chat/${newThread}`)
  }

  const currentTitle = () => {
    // 侧栏里的标题最准 —— 没有就退回第一条用户消息
    const known = sessions.find((item) => item.threadId === threadId)
    if (known) return known.title
    const first = chat.messages.find((item) => item.role === 'user')
    return first ? titleFromMessage(first.content) : '新会话'
  }

  const send = async (text: string, attachments: string[], providerId: number | null) => {
    const isNew = !routeThreadId
    streamingThreadRef.current = threadId
    // **先建会话、先跳路由，别等流跑完** —— 流式期间侧栏看不到这个会话，
    // 用户这时刷新页面，draft id 会重新随机生成，后端的 checkpoint 就成了孤儿，
    // 再也进不来了。
    touch(threadId, titleFromMessage(text))
    if (isNew) {
      navigate(`/chat/${threadId}`, { replace: true })
    }
    // 新的一轮从底下开始看
    stickToBottomRef.current = true
    await chat.send(text, threadId, { attachments, providerId })
    // **别把新会话的标记抹掉** —— 切走时旧会话这一行会晚一步才结束
    if (streamingThreadRef.current === threadId) streamingThreadRef.current = null
  }

  const handleScroll = () => {
    const element = scrollRef.current
    if (!element) return
    stickToBottomRef.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < STICK_TO_BOTTOM_SLACK
  }

  /** 往上再加载一批更早的消息。 */
  const loadEarlier = () => {
    // **先记下现在的高度** —— 加载后要把滚动位置补回去
    heightBeforeLoadRef.current = scrollRef.current?.scrollHeight ?? 0
    setVisibleCount((count) => count + PAGE_SIZE)
  }

  // 加载更早的消息后，把滚动位置补回去 —— 用户看到的还是刚才那条。
  //
  // **放在自动滚底之后** —— 补位置要赢过"滚到底"，不然两条规则会打架。
  useLayoutEffect(() => {
    const element = scrollRef.current
    const before = heightBeforeLoadRef.current
    if (!element || !before) return
    heightBeforeLoadRef.current = 0
    const delta = element.scrollHeight - before
    if (delta > 0) element.scrollTop += delta
  }, [visibleCount, chat.messages])

  // 新内容进来就滚到底。
  //
  // **用 layout effect 而不是 effect**：流式时消息更新比帧还快，
  // requestAnimationFrame 每次都被下一次更新取消掉，永远轮不上（页面不动）。
  // layout effect 在 DOM 变更后同步执行，这时 scrollHeight 已经是新高度。
  useLayoutEffect(() => {
    const element = scrollRef.current
    if (!element) return
    // **用户已经上滑了就别拽回去**
    if (!stickToBottomRef.current) return
    element.scrollTop = element.scrollHeight
    // 再补一帧：插入新节点后 flex 布局可能才最终定高，那时再滚一次才到底。
    // （同步那次已经把绝大多数情况覆盖了，这帧只是兜底。）
    const frame = requestAnimationFrame(() => {
      element.scrollTop = element.scrollHeight
    })
    return () => cancelAnimationFrame(frame)
  }, [chat.messages, chat.busy])

  const statusHint = chat.error

  return (
    <div
      className={[
        'relative flex h-full flex-col',
        // 空状态整组居中：标题、输入框、推荐问题挤在一块儿放中间
        empty ? 'justify-center gap-8' : '',
      ].join(' ')}
    >
      {empty ? (
        <>
          {/* 背景：中心光晕 + 点阵（只在这一个空状态页上有） */}
          <div aria-hidden className="hero-glow pointer-events-none absolute inset-0" />
          <div aria-hidden className="hero-grid pointer-events-none absolute inset-0" />
        </>
      ) : null}

      {/* 消息区 —— 空的时候没内容，不占位 */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className={empty ? 'hidden' : 'min-h-0 flex-1 overflow-y-auto'}
      >
        <MessageList
          messages={chat.messages}
          busy={chat.busy}
          visibleCount={visibleCount}
          onLoadEarlier={loadEarlier}
          onApprove={(decisions) => void chat.approve(threadId, decisions)}
          onReject={(decisions) => void chat.reject(threadId, decisions)}
          onRegenerate={() => void chat.regenerate(threadId)}
          onFork={(sourceId) => void handleFork(sourceId)}
        />
      </div>

      {/* 空状态的标题 */}
      {empty ? (
        <div className="relative px-6 text-center">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">
            在你的代码工作区里干活
          </h1>
          <p className="mt-2 text-sm text-ink-muted">
            读代码、改代码、跑命令、给结果 —— 或者直接问。
          </p>
        </div>
      ) : null}

      {/*
        输入框 —— **只渲染这一个**。
        以前空状态与底部各渲染一个，切过去就卸载重挂，模型选择回到默认、
        已打的字也没了。现在只换位置（`centered`），组件不卸载。
      */}
      <div className={empty ? 'relative' : 'shrink-0'}>
        {/* 错误条提到页面级 —— 与空状态无关，空白页也得让人看见报错 */}
        {statusHint ? (
          <div className="mx-auto max-w-4xl px-6 pb-1 text-xs text-ink-faint">{statusHint}</div>
        ) : null}
        <Composer
          ref={composerRef}
          threadId={threadId}
          centered={empty}
          busy={chat.busy}
          onSend={(text, attachments, providerId) => void send(text, attachments, providerId)}
          onStop={chat.abort}
          onCommandAction={(action) => void runCommandAction(action)}
          onOpenDetail={() => openStatusPanel(threadId)}
        />
      </div>

      {/* 工作目录条**只在初始状态**出现，放在对话框下方 ——
          开新会话时选好目录，聊起来之后它就不占地方了
          （要换目录去「工作目录」页）。 */}
      {empty ? (
        <div className="relative mx-auto w-full max-w-4xl px-6">
          <div className="mt-2">
            <EnvironmentBar threadId={threadId} />
          </div>
        </div>
      ) : null}

      {empty ? (
        chat.loading ? (
          <div className="relative px-6 text-center text-sm text-ink-muted">正在加载对话…</div>
        ) : (
          <div className="relative mx-auto flex max-w-4xl flex-wrap justify-center gap-2 px-6">
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => composerRef.current?.setText(suggestion)}
                className="rounded-xl border border-line bg-sunken/70 px-3.5 py-2 text-left text-xs text-ink-muted transition-colors hover:border-accent/40 hover:bg-accent-soft/50 hover:text-ink"
              >
                {suggestion}
              </button>
            ))}
          </div>
        )
      ) : null}

      {confirmDialog}
      {promptDialog}
    </div>
  )
}
