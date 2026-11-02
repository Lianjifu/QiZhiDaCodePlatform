/**
 * 应用外壳：左侧栏 + 右侧内容区。
 *
 * 侧栏的分组照着 Claude 桌面端来：**动作 / 模块入口 / 置顶 / 全部会话**。
 * 模块入口对应平台真实的模块 —— 没有对应东西的就不放（比如「项目」）。
 */
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'

import ThemeToggle from './ThemeToggle'
import { useSessions } from '../../features/sessions/store'

interface NavItem {
  to: string
  label: string
  hint: string
}

const MODULES: NavItem[] = [
  { to: '/chat', label: '编码助手', hint: '对话 / 工具调用 / 审批' },
  { to: '/files', label: '文件', hint: '工作区文件与上传件' },
  { to: '/capabilities', label: '能力清单', hint: '工具 / 技能 / MCP' },
  { to: '/providers', label: '模型接入', hint: '供应商配置' },
]

export default function AppShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const { pinned, others, togglePin } = useSessions()

  // 当前会话：从路由里认出来
  const activeThread = location.pathname.startsWith('/chat/')
    ? location.pathname.slice('/chat/'.length)
    : null

  return (
    <div className="flex h-screen overflow-hidden bg-base">
      <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-sunken">
        <div className="px-3 py-3">
          <button
            type="button"
            onClick={() => navigate('/chat')}
            className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-ink transition-colors hover:bg-hover"
          >
            <span className="text-lg leading-none">+</span>
            <span>新建</span>
          </button>
          <button
            type="button"
            onClick={() => navigate('/chat')}
            className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-ink-muted transition-colors hover:bg-hover hover:text-ink"
          >
            <span className="text-base leading-none">⌕</span>
            <span>搜索</span>
          </button>
        </div>

        <nav className="px-3 pb-2">
          {MODULES.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/chat'}
              className={({ isActive }) =>
                [
                  'block rounded-lg px-2.5 py-2 transition-colors',
                  isActive ? 'bg-hover text-ink' : 'text-ink-muted hover:bg-hover hover:text-ink',
                ].join(' ')
              }
            >
              <div className="text-sm font-medium">{item.label}</div>
              <div className="text-xs text-ink-faint">{item.hint}</div>
            </NavLink>
          ))}
        </nav>

        <div className="mt-1 min-h-0 flex-1 overflow-y-auto px-3 pb-2">
          {pinned.length > 0 && (
            <SessionGroup
              title="已置顶"
              items={pinned}
              activeThread={activeThread}
              onSelect={(threadId) => navigate(`/chat/${threadId}`)}
              onTogglePin={togglePin}
            />
          )}
          <SessionGroup
            title="对话和任务"
            items={others}
            activeThread={activeThread}
            onSelect={(threadId) => navigate(`/chat/${threadId}`)}
            onTogglePin={togglePin}
          />
        </div>

        <div className="border-t border-line p-3">
          <ThemeToggle />
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-hidden bg-base">
        <Outlet />
      </main>
    </div>
  )
}

interface SessionGroupProps {
  title: string
  items: { threadId: string; title: string; pinned: boolean }[]
  activeThread: string | null
  onSelect: (threadId: string) => void
  onTogglePin: (threadId: string) => void
}

function SessionGroup({
  title,
  items,
  activeThread,
  onSelect,
  onTogglePin,
}: SessionGroupProps) {
  return (
    <div className="mb-3">
      <div className="px-2.5 pb-1 text-xs font-medium tracking-wide text-ink-faint">{title}</div>
      {items.length === 0 ? (
        <div className="px-2.5 py-1 text-xs text-ink-faint">（空）</div>
      ) : (
        items.map((item) => (
          <div
            key={item.threadId}
            className={[
              'group flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-sm transition-colors',
              item.threadId === activeThread
                ? 'bg-hover text-ink'
                : 'text-ink-muted hover:bg-hover hover:text-ink',
            ].join(' ')}
          >
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
          </div>
        ))
      )}
    </div>
  )
}
