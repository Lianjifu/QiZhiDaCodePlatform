/**
 * 路由表。
 *
 * 现在只有「系统状态」是实的，其余是占位 —— 后端接口都在，但页面按功能逐个加。
 */
import { Route, Routes } from 'react-router'

import AppShell from './components/layout/AppShell'
import PlaceholderPage from './pages/PlaceholderPage'
import StatusPage from './pages/StatusPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<StatusPage />} />
        <Route path="chat" element={<PlaceholderPage module="编码助手" stage="阶段 1" />} />
        <Route path="files" element={<PlaceholderPage module="文件" stage="阶段 2" />} />
        <Route path="providers" element={<PlaceholderPage module="模型接入配置" stage="阶段 3" />} />
        <Route path="capabilities" element={<PlaceholderPage module="能力清单" stage="阶段 3" />} />
        <Route path="*" element={<PlaceholderPage module="页面" stage="未规划" />} />
      </Route>
    </Routes>
  )
}
