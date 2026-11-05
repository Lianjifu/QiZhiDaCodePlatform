/**
 * 应用外壳：左侧栏 + 中间内容区 + 右侧状态列。
 *
 * 侧栏的分组照着 Claude 桌面端来：**产品头 / 动作 / 模块入口 / 全部会话**。
 * 模块入口对应平台真实的模块 —— 没有对应东西的就不放（比如「项目」）。
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'

import {
  AutoIcon,
  BranchIcon,
  ChevronIcon,
  FolderIcon,
  LayersIcon,
  MoonIcon,
  PanelLeftIcon,
  PlugIcon,
  PlusIcon,
  SearchIcon,
  SunIcon,
} from '../../components/icons'
import ThemeToggle from './ThemeToggle'
import CommandPalette, { type PaletteCommand } from '../../features/commands/CommandPalette'
import SessionStatusPanel from '../../features/sessions/SessionStatusPanel'
import { useSessions } from '../../features/sessions/store'
import { useTheme } from '../../theme/ThemeProvider'
import {
  parseSections,
  serializeSections,
  toggleSection,
  SECTIONS_KEY,
  type SectionId,
  type SectionState,
} from './sidebarSections'

interface NavItem {
  to: string
  label: string
  hint: string
  icon: ReactNode
}

/**
 * 模块入口。
 *
 * **这里没有「编码助手」**：新建会话就是进入对话，单独再放一个入口是重复的。
 * 会话列表里点任意一条也能回到对话。
 */
const MODULES: NavItem[] = [
  {
    to: '/files',
    label: '文件',
    hint: '工作区文件与上传件',
    icon: <FolderIcon className="h-4 w-4" />,
  },
  {
    to: '/environments',
    label: '工作目录',
    hint: '仓库与 worktree',
    icon: <BranchIcon className="h-4 w-4" />,
  },
  {
    to: '/capabilities',
    label: '能力清单',
    hint: '工具 / 技能 / MCP',
    icon: <LayersIcon className="h-4 w-4" />,
  },
  {
    to: '/providers',
    label: '模型接入',
    hint: '供应商配置',
    icon: <PlugIcon className="h-4 w-4" />,
  },
  {
    to: '/retrospectives',
    label: '进化记录',
    hint: 'agent 改了什么',
    icon: <LayersIcon className="h-4 w-4" />,
  },
]

/** 侧栏折叠状态存在哪儿 —— 刷新页面不该把用户的布局弹回去。 */
const SIDEBAR_KEY = 'qzd.sidebar.collapsed'


export default function AppShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const { pinned, others, create, remove, togglePin } = useSessions()
  const { setMode } = useTheme()
  const [searchOpen, setSearchOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [statusOpen, setStatusOpen] = useState(false)
  /**
   * 侧栏折叠没折叠。
   *
   * 窄屏下那一栏会把对话挤没 —— 折叠起来就只剩一条图标轨。
   * 状态记在 localStorage 里：布局是用户自己的选择，刷新不该弹回去。
   */
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_KEY) === '1',
  )
  /**
   * 各分区（工作区 / 已置顶 / 对话和任务）折没折。
   *
   * 会话一多，会话列表能把模块入口整个顶出屏幕 —— 折起来只留标题，
   * 想看再点开。跟整条侧栏的折叠是两回事，各自存各自的。
   */
  const [sections, setSections] = useState<SectionState>(
    () => parseSections(localStorage.getItem(SECTIONS_KEY)),
  )
  const toggleSectionById = useCallback((id: SectionId) => {
    setSections((state) => toggleSection(state, id))
  }, [])
  /**
   * 窄屏上侧栏是抽屉 —— 屏幕就那么宽，固定占一栏就没地方放内容了。
   */
  const [drawerOpen, setDrawerOpen] = useState(false)
  /**
   * 状态面板看哪个会话。
   *
   * 一般就是路由上的那个会话；但空会话页（`/chat`）还没落到路由上，
   * 那个"草稿会话"也得能看 —— 所以允许显式指定。
   */
  const [statusThread, setStatusThread] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  // 当前会话：从路由里认出来
  const activeThread = location.pathname.startsWith('/chat/')
    ? location.pathname.slice('/chat/'.length)
    : null

  /**
   * 跳转 —— 顺带把抽屉收起来。
   *
   * **不用 effect 盯着路由变化**：那会多一次渲染，而且手机上收抽屉这件事
   * 本来就跟着"用户点了某个入口"走。
   */
  const go = useCallback(
    (to: string) => {
      setDrawerOpen(false)
      navigate(to)
    },
    [navigate],
  )

  /** 新建一个会话 —— 立刻进列表，不是等用户发了第一条消息才进。 */
  const handleCreate = () => {
    const threadId = create()
    go(`/chat/${threadId}`)
  }

  /** 删一个会话（只删侧栏里的这条记录）。 */
  const handleRemove = (threadId: string) => {
    remove(threadId)
    if (threadId === activeThread) go('/chat')
  }

  // 搜索：按标题过滤。空搜索词就是全列。
  const keyword = query.trim().toLowerCase()
  const match = (items: typeof others) =>
    keyword ? items.filter((item) => item.title.toLowerCase().includes(keyword)) : items
  const pinnedShown = match(pinned)
  const othersShown = match(others)

  // 面板看哪个会话：显式指定过的优先，否则就是路由上的那个
  const panelThread = statusThread ?? activeThread

  /**
   * ⌘K / Ctrl+K —— 打开命令面板。
   *
   * 在输入框里按 ⌘K 不该弹面板（那是用户要输内容的地方），所以先看
   * 焦点是不是落在输入框 / 文本域里。
   */
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const tag = target?.tagName?.toLowerCase()
      const typing = tag === 'input' || tag === 'textarea' || target?.isContentEditable
      if (event.metaKey || event.ctrlKey) {
        // ⌘B —— 折叠 / 展开侧栏
        if (event.key.toLowerCase() === 'b' && !typing) {
          event.preventDefault()
          setCollapsed((value) => !value)
          return
        }
        // ⌘K —— 命令面板
        if (event.key.toLowerCase() === 'k' && !typing) {
          event.preventDefault()
          setPaletteOpen((value) => !value)
        }
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  // 记住折叠状态
  useEffect(() => {
    localStorage.setItem(SIDEBAR_KEY, collapsed ? '1' : '0')
  }, [collapsed])

  // 记住各分区的折叠状态
  useEffect(() => {
    localStorage.setItem(SECTIONS_KEY, serializeSections(sections))
  }, [sections])

  /** 命令清单 —— 面板里按关键词过滤。 */
  const commands = useMemo<PaletteCommand[]>(
    () => [
      {
        id: 'new-session',
        title: '新建会话',
        hint: '开一个空白对话',
        icon: <PlusIcon className="h-3.5 w-3.5" />,
        action: () => {
          const threadId = create()
          go(`/chat/${threadId}`)
        },
      },
      {
        id: 'go-files',
        title: '工作区文件',
        hint: '文件与上传件',
        icon: <FolderIcon className="h-3.5 w-3.5" />,
        action: () => go(activeThread ? `/files?thread=${activeThread}` : '/files'),
      },
      {
        id: 'go-environments',
        title: '工作目录',
        hint: '仓库 / worktree',
        icon: <BranchIcon className="h-3.5 w-3.5" />,
        action: () => go('/environments'),
      },
      {
        id: 'go-capabilities',
        title: '能力清单',
        hint: '工具 / 技能 / MCP',
        icon: <LayersIcon className="h-3.5 w-3.5" />,
        action: () => go('/capabilities'),
      },
      {
        id: 'go-providers',
        title: '模型接入',
        hint: '供应商配置',
        icon: <PlugIcon className="h-3.5 w-3.5" />,
        action: () => go('/providers'),
      },
      {
        id: 'go-retrospectives',
        title: '进化记录',
        hint: 'agent 自己改了什么',
        icon: <LayersIcon className="h-3.5 w-3.5" />,
        action: () => go('/retrospectives'),
      },
      {
        id: 'toggle-sidebar',
        title: collapsed ? '展开侧栏' : '折叠侧栏',
        hint: '⌘B',
        icon: <PanelLeftIcon className="h-3.5 w-3.5" />,
        action: () => setCollapsed((value) => !value),
      },
      {
        id: 'toggle-panel',
        title: '会话状态面板',
        hint: '打开右侧那一列',
        icon: <LayersIcon className="h-3.5 w-3.5" />,
        action: () => setStatusOpen(true),
      },
      {
        id: 'theme-light',
        title: '切换到浅色',
        hint: '主题',
        icon: <SunIcon className="h-3.5 w-3.5" />,
        action: () => setMode('light'),
      },
      {
        id: 'theme-dark',
        title: '切换到深色',
        hint: '主题',
        icon: <MoonIcon className="h-3.5 w-3.5" />,
        action: () => setMode('dark'),
      },
      {
        id: 'theme-system',
        title: '主题跟随系统',
        hint: '主题',
        icon: <AutoIcon className="h-3.5 w-3.5" />,
        action: () => setMode('system'),
      },
    ],
    [activeThread, collapsed, create, go, setMode],
  )

  /** 模块入口的跳转地址 —— 文件是按会话隔离的，要带上当前会话。 */
  const moduleTo = (to: string) =>
    to === '/files' && activeThread ? `/files?thread=${activeThread}` : to

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-base">
      {/*
        窄屏顶栏 —— 侧栏收成抽屉之后，得有个地方放品牌和开关。
        宽屏不需要它（侧栏自己就是导航）。
      */}
      <div className="flex h-13 shrink-0 items-center gap-2 border-b border-line bg-base px-3 py-2 lg:hidden">
        <button
          type="button"
          title="打开导航"
          onClick={() => setDrawerOpen(true)}
          className="-ml-1 flex h-9 w-9 items-center justify-center rounded-lg text-ink-muted transition-colors hover:bg-hover hover:text-ink"
        >
          <PanelLeftIcon className="h-4.5 w-4.5" />
        </button>
        <span
          className="flex h-6 w-6 items-center justify-center rounded-md text-xs font-semibold text-accent-text"
          style={{ background: 'var(--brand-gradient)' }}
        >
          智
        </span>
        <span className="truncate text-sm font-medium text-ink">企智搭·代码平台</span>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* 抽屉的遮罩 —— 点一下收起 */}
        {drawerOpen ? (
          <div
            className="fixed inset-0 z-40 bg-black/40 lg:hidden"
            onClick={() => setDrawerOpen(false)}
          />
        ) : null}

        <aside
          className={[
            'z-50 flex shrink-0 flex-col border-r border-line bg-sunken',
            // 窄屏：从左边滑进来的抽屉；宽屏：固定的一栏
            'fixed inset-y-0 left-0 w-64 transition-transform duration-200 lg:static lg:translate-x-0',
            drawerOpen ? 'translate-x-0' : '-translate-x-full',
            // 宽屏才有折叠 —— 窄屏上一律是完整的一栏
            collapsed ? 'lg:w-14' : 'lg:w-64',
          ].join(' ')}
        >
        {collapsed ? (
          <>
            {/* 折叠态：一条图标轨 —— 只留入口，标题和会话列表都收起来 */}
            <div className="hidden flex-1 flex-col items-center gap-1 py-3 lg:flex">
              <button
                type="button"
                title="展开侧栏（⌘B）"
                onClick={() => setCollapsed(false)}
                className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-muted transition-colors hover:bg-hover hover:text-ink"
              >
                <PanelLeftIcon className="h-4 w-4" />
              </button>
              <button
                type="button"
                title="新建会话"
                onClick={handleCreate}
                className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-muted transition-colors hover:bg-hover hover:text-ink"
              >
                <PlusIcon className="h-4 w-4" />
              </button>
              <button
                type="button"
                title="搜索"
                // 搜索框在展开态才有 —— 点了先把侧栏展开
                onClick={() => {
                  setCollapsed(false)
                  setSearchOpen(true)
                }}
                className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-muted transition-colors hover:bg-hover hover:text-ink"
              >
                <SearchIcon className="h-4 w-4" />
              </button>
              <div className="my-1 h-px w-6 bg-line" />
              {MODULES.map((item) => (
                <NavLink
                  key={item.to}
                  to={moduleTo(item.to)}
                  end
                  title={item.label}
                  onClick={() => setDrawerOpen(false)}
                  className={({ isActive }) =>
                    [
                      'flex h-9 w-9 items-center justify-center rounded-lg transition-colors',
                      isActive ? 'bg-hover text-ink' : 'text-ink-faint hover:bg-hover hover:text-ink',
                    ].join(' ')
                  }
                >
                  {item.icon}
                </NavLink>
              ))}
            </div>
            <div className="hidden border-t border-line p-1.5 lg:block">
              <ThemeToggle compact />
            </div>
          </>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* 产品头 */}
            <div className="flex items-center gap-2.5 px-4 pb-2 pt-4">
              <div
                className="flex h-7 w-7 items-center justify-center rounded-lg text-sm font-semibold text-accent-text"
                style={{ background: 'var(--brand-gradient)' }}
              >
                智
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-ink">企智搭·代码平台</div>
              </div>
              <button
                type="button"
                title="折叠侧栏（⌘B）"
                onClick={() => setCollapsed(true)}
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-ink-faint transition-colors hover:bg-hover hover:text-ink"
              >
                <PanelLeftIcon className="h-4 w-4" />
              </button>
            </div>

            <div className="px-3 pb-3">
              <button
                type="button"
                onClick={handleCreate}
                className="flex w-full items-center gap-2 rounded-lg bg-raised px-2.5 py-2 text-sm font-medium text-ink shadow-1 transition-colors hover:bg-hover"
              >
                <PlusIcon className="h-4 w-4" />
                <span>新建会话</span>
              </button>
              {searchOpen ? (
                <div className="mt-2 px-1">
                  <input
                    autoFocus
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Escape') {
                        setQuery('')
                        setSearchOpen(false)
                      }
                    }}
                    placeholder="搜会话标题…"
                    className="w-full rounded-lg border border-line bg-base px-2.5 py-1.5 text-sm text-ink outline-none placeholder:text-ink-faint"
                  />
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setSearchOpen(true)}
                  className="mt-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-ink-muted transition-colors hover:bg-hover hover:text-ink"
                >
                  <SearchIcon className="h-4 w-4" />
                  <span>搜索</span>
                  <span className="flex-1" />
                  <kbd className="rounded border border-line px-1.5 py-0.5 text-[10px] text-ink-faint">
                    ⌘K
                  </kbd>
                </button>
              )}
            </div>

            {/* 模块入口 */}
            <nav className="px-3 pb-3">
              <SectionHeader
                title="工作区"
                collapsed={Boolean(sections.workspace)}
                onToggle={() => toggleSectionById('workspace')}
              />
              {sections.workspace ? null : (
                <>
                  {MODULES.map((item) => (
                    <NavLink
                      key={item.to}
                      // 文件是按会话隔离的 —— 从会话里点「文件」要把当前会话带过去，
                      // 不然看到的是另一个会话的工作区
                      to={moduleTo(item.to)}
                      end
                      onClick={() => setDrawerOpen(false)}
                      className={({ isActive }) =>
                        [
                          'flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition-colors',
                          isActive ? 'bg-hover text-ink' : 'text-ink-muted hover:bg-hover hover:text-ink',
                        ].join(' ')
                      }
                    >
                      <span className="shrink-0 text-ink-faint">{item.icon}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">{item.label}</span>
                        <span className="block truncate text-[11px] text-ink-faint">{item.hint}</span>
                      </span>
                    </NavLink>
                  ))}
                </>
              )}
            </nav>

            {/* 会话列表 */}
            <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-2">
              {pinnedShown.length > 0 && (
                <SessionGroup
                  title="已置顶"
                  id="pinned"
                  collapsed={Boolean(sections.pinned)}
                  onToggle={toggleSectionById}
                  items={pinnedShown}
                  activeThread={activeThread}
                  onSelect={(threadId) => go(`/chat/${threadId}`)}
                  onTogglePin={togglePin}
                  onRemove={handleRemove}
                />
              )}
              <SessionGroup
                title="对话和任务"
                id="sessions"
                collapsed={Boolean(sections.sessions)}
                onToggle={toggleSectionById}
                items={othersShown}
                activeThread={activeThread}
                onSelect={(threadId) => go(`/chat/${threadId}`)}
                onTogglePin={togglePin}
                onRemove={handleRemove}
              />
              {pinnedShown.length === 0 && othersShown.length === 0 && query.trim() ? (
                <div className="px-2 py-1 text-xs text-ink-faint">没有匹配的会话</div>
              ) : null}
            </div>

            <div className="border-t border-line p-2">
              <ThemeToggle />
            </div>
          </div>
        )}
      </aside>

      <main className="min-w-0 flex-1 overflow-hidden bg-base">
        <Outlet
          context={{
            openStatusPanel: (threadId?: string) => {
              setStatusThread(threadId ?? null)
              setStatusOpen(true)
            },
          }}
        />
      </main>

      {/*
        右侧的状态面板 —— 占一整列，中间是对话区（参考 Claude 桌面端的布局）。
        面板自己滚，不影响对话区。**窄屏上占满整屏** —— 一栏 22rem 在手机上
        就是全屏，没必要再并排放。
      */}
      {paletteOpen ? (
        <CommandPalette commands={commands} onClose={() => setPaletteOpen(false)} />
      ) : null}

      {statusOpen ? (
        <aside className="flex w-full shrink-0 flex-col overflow-y-auto border-l border-line bg-sunken p-3 lg:w-[22rem]">
          <div className="mb-2 flex items-center justify-between px-1">
            <span className="text-xs font-medium text-ink-muted">会话状态</span>
            <button
              type="button"
              title="收起面板"
              onClick={() => setStatusOpen(false)}
              className="rounded px-1 text-xs text-ink-faint transition-colors hover:bg-hover hover:text-ink"
            >
              ×
            </button>
          </div>
          {panelThread ? (
            <SessionStatusPanel threadId={panelThread} />
          ) : (
            <div className="rounded-xl border border-line bg-raised px-3 py-2.5 text-xs text-ink-faint">
              还没有会话 —— 进一个会话才能看它的状态。
            </div>
          )}
        </aside>
      ) : null}
      </div>
    </div>
  )
}

interface SectionHeaderProps {
  title: string
  collapsed: boolean
  onToggle: () => void
}

/**
 * 分区标题 —— 点一下折起 / 展开。
 *
 * **为什么标题要能点**：侧栏里最占地方的是会话列表和模块入口，
 * 分区折起来以后只剩一行标题，想看再点开。
 */
function SectionHeader({ title, collapsed, onToggle }: SectionHeaderProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={!collapsed}
      className="mb-1 flex w-full items-center gap-1 px-2.5 text-left text-[11px] font-medium tracking-wide text-ink-faint transition-colors hover:text-ink-muted"
    >
      <ChevronIcon
        className={[
          'h-3 w-3 shrink-0 transition-transform',
          // 图标本身朝下 —— 折起来时转成朝右
          collapsed ? '-rotate-90' : '',
        ].join(' ')}
      />
      <span className="flex-1">{title}</span>
    </button>
  )
}

interface SessionGroupProps {
  title: string
  /** 分区 id —— 折叠状态记在它名下 */
  id: SectionId
  collapsed: boolean
  onToggle: (id: SectionId) => void
  items: { threadId: string; title: string; pinned: boolean }[]
  activeThread: string | null
  onSelect: (threadId: string) => void
  onTogglePin: (threadId: string) => void
  onRemove: (threadId: string) => void
}

function SessionGroup({
  title,
  id,
  collapsed,
  onToggle,
  items,
  activeThread,
  onSelect,
  onTogglePin,
  onRemove,
}: SessionGroupProps) {
  return (
    <div className="mb-3">
      <SectionHeader title={title} collapsed={collapsed} onToggle={() => onToggle(id)} />
      {collapsed ? null : items.length === 0 ? (
        <div className="px-2.5 py-1 text-xs text-ink-faint">（空）</div>
      ) : (
        items.map((item) => {
          const active = item.threadId === activeThread
          return (
            <div
              key={item.threadId}
              className={[
                'group flex items-center gap-1 rounded-lg py-1.5 pr-1.5 pl-2.5 text-sm transition-colors',
                active
                  ? 'bg-hover text-ink'
                  : 'text-ink-muted hover:bg-hover/60 hover:text-ink',
              ].join(' ')}
            >
              {/* 当前会话：左侧一根竖线 —— 比底色更安静，也更好找 */}
              <span
                className={[
                  'mr-0.5 h-4 w-0.5 shrink-0 rounded-full transition-opacity',
                  active ? 'bg-accent opacity-100' : 'opacity-0',
                ].join(' ')}
              />
              <button
                type="button"
                onClick={() => onSelect(item.threadId)}
                className="min-w-0 flex-1 truncate text-left"
              >
                {item.title}
              </button>
              <button
                type="button"
                title={item.pinned ? '取消置顶' : '置顶'}
                onClick={() => onTogglePin(item.threadId)}
                className="shrink-0 rounded px-1 text-xs text-ink-faint opacity-0 transition-opacity group-hover:opacity-100 hover:text-ink"
              >
                {item.pinned ? '★' : '☆'}
              </button>
              <button
                type="button"
                title="删除会话"
                onClick={() => onRemove(item.threadId)}
                className="shrink-0 rounded px-1 text-xs text-ink-faint opacity-0 transition-opacity group-hover:opacity-100 hover:text-danger"
              >
                ×
              </button>
            </div>
          )
        })
      )}
    </div>
  )
}
