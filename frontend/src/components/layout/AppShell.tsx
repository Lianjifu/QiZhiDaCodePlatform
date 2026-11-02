/**
 * 应用外壳：左侧导航 + 右侧内容区。
 *
 * 导航项按**平台模块**排，不按页面排 —— 后端那些 router 就是模块的边界，
 * 以后加功能只是在对应模块下加页面。
 */
import { NavLink, Outlet } from 'react-router'

interface NavItem {
  to: string
  label: string
  hint: string
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '系统状态', hint: '前后端联通检查' },
  { to: '/chat', label: '编码助手', hint: '对话 / 工具调用 / 审批' },
  { to: '/files', label: '文件', hint: '工作区文件与上传件' },
  { to: '/providers', label: '模型接入', hint: '供应商配置' },
  { to: '/capabilities', label: '能力清单', hint: '工具 / 技能 / MCP' },
]

export default function AppShell() {
  return (
    <div className="flex min-h-screen">
      <aside className="w-60 shrink-0 border-r border-slate-200 bg-white">
        <div className="px-5 py-5">
          <div className="text-base font-semibold tracking-tight">企智搭·代码平台</div>
          <div className="mt-1 text-xs text-slate-500">前端控制台</div>
        </div>
        <nav className="px-3 pb-4">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                [
                  'block rounded-lg px-3 py-2 transition-colors',
                  isActive
                    ? 'bg-brand-50 text-brand-700'
                    : 'text-slate-700 hover:bg-slate-100',
                ].join(' ')
              }
            >
              <div className="text-sm font-medium">{item.label}</div>
              <div className="text-xs text-slate-400">{item.hint}</div>
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}
