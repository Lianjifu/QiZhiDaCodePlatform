/**
 * 应用外壳：左侧栏 + 中间内容区 + 右侧状态列。
 *
 * 侧栏的分组照着 Claude 桌面端来：**产品头 / 动作 / 模块入口 / 全部会话**。
 * 模块入口对应平台真实的模块 —— 没有对应东西的就不放（比如「项目」）。
 */
import { useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'

import { ChatIcon, FolderIcon, LayersIcon, PlugIcon, PlusIcon, SearchIcon } from '../../components/icons'
import ThemeToggle from './ThemeToggle'
import SessionStatusPanel from '../../features/sessions/SessionStatusPanel'
import { useSessions } from '../../features/sessions/store'

interface NavItem {
  to: string
  label: string
  hint: string
  icon: ReactNode
}

const MODULES: NavItem[] = [
  {
    to: '/chat',
    label: '编码助手',
    hint: '对话 / 工具调用 / 审批',
    icon: <ChatIcon className="h-4 w-4" />,
  },
  {
    to: '/files',
    label: '文件',
    hint: '工作区文件与上传件',
    icon: <FolderIcon className="h-4 w-4" />,
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
]

export default function AppShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const { pinned, others, create, remove, togglePin } = useSessions()
  const [searchOpen, setSearchOpen] = useState(false)
  const [statusOpen, setStatusOpen] = useState(false)
  const [query, setQuery] = useState('')

  // 当前会话：从路由里认出来
  const activeThread = location.pathname.startsWith('/chat/')
    ? location.pathname.slice('/chat/'.length)
    : null

  /** 新建一个会话 —— 立刻进列表，不是等用户发了第一条消息才进。 */
  const handleCreate = () => {
    const threadId = create()
    navigate(`/chat/${threadId}`)
  }

  /** 删一个会话（只删侧栏里的这条记录）。 */
  const handleRemove = (threadId: string) => {
    remove(threadId)
    if (threadId === activeThread) navigate('/chat')
  }

  // 搜索：按标题过滤。空搜索词就是全列。
  const keyword = query.trim().toLowerCase()
  const match = (items: typeof others) =>
    keyword ? items.filter((item) => item.title.toLowerCase().includes(keyword)) : items
  const pinnedShown = match(pinned)
  const othersShown = match(others)

  return (
    <div className="flex h-screen overflow-hidden bg-base">
      <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-sunken">
        {/* 产品头 */}
        <div className="flex items-center gap-2.5 px-4 pb-2 pt-4">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-sm font-semibold text-accent-text">
            智
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-ink">企智搭·代码平台</div>
          </div>
        </div>

        <div className="px-3 pb-3">
          <button
            type="button"
            onClick={handleCreate}
            className="flex w-full items-center gap-2 rounded-lg bg-raised px-2.5 py-2 text-sm font-medium text-ink shadow-[0_1px_2px_oklch(0_0_0/0.06)] transition-colors hover:bg-hover"
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
            </button>
          )}
        </div>

        {/* 模块入口 */}
        <nav className="px-3 pb-3">
          <div className="mb-1 px-2.5 text-[11px] font-medium tracking-wide text-ink-faint">
            工作区
          </div>
          {MODULES.map((item) => (
            <NavLink
              key={item.to}
              // 文件是按会话隔离的 —— 从会话里点「文件」要把当前会话带过去，
              // 不然看到的是另一个会话的工作区
              to={item.to === '/files' && activeThread ? `/files?thread=${activeThread}` : item.to}
              end={item.to === '/chat'}
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
        </nav>

        {/* 会话列表 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-2">
          {pinnedShown.length > 0 && (
            <SessionGroup
              title="已置顶"
              items={pinnedShown}
              activeThread={activeThread}
              onSelect={(threadId) => navigate(`/chat/${threadId}`)}
              onTogglePin={togglePin}
              onRemove={handleRemove}
            />
          )}
          <SessionGroup
            title="对话和任务"
            items={othersShown}
            activeThread={activeThread}
            onSelect={(threadId) => navigate(`/chat/${threadId}`)}
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
      </aside>

      <main className="min-w-0 flex-1 overflow-hidden bg-base">
        <Outlet context={{ openStatusPanel: () => setStatusOpen(true) }} />
      </main>

      {/*
        右侧的状态面板 —— 占一整列，中间是对话区（参考 Claude 桌面端的布局）。
        面板自己滚，不影响对话区。
      */}
      {statusOpen ? (
        <aside className="flex w-[22rem] shrink-0 flex-col overflow-y-auto border-l border-line bg-sunken p-3">
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
          {activeThread ? (
            <SessionStatusPanel threadId={activeThread} />
          ) : (
            <div className="rounded-xl border border-line bg-raised px-3 py-2.5 text-xs text-ink-faint">
              还没有会话 —— 进一个会话才能看它的状态。
            </div>
          )}
        </aside>
      ) : null}
    </div>
  )
}

interface SessionGroupProps {
  title: string
  items: { threadId: string; title: string; pinned: boolean }[]
  activeThread: string | null
  onSelect: (threadId: string) => void
  onTogglePin: (threadId: string) => void
  onRemove: (threadId: string) => void
}

function SessionGroup({
  title,
  items,
  activeThread,
  onSelect,
  onTogglePin,
  onRemove,
}: SessionGroupProps) {
  return (
    <div className="mb-3">
      <div className="mb-1 px-2.5 text-[11px] font-medium tracking-wide text-ink-faint">{title}</div>
      {items.length === 0 ? (
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
