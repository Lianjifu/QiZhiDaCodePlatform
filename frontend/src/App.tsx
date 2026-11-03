/**
 * 路由表。
 *
 * **只有「编码助手」支持多轮对话** —— 其余页面是配置与查看。
 */
import { Navigate, Route, Routes } from 'react-router'

import AppShell from './components/layout/AppShell'
import CapabilitiesPage from './pages/CapabilitiesPage'
import ChatPage from './features/agent/ChatPage'
import EnvironmentsPage from './pages/EnvironmentsPage'
import FilesPage from './pages/FilesPage'
import PlaceholderPage from './pages/PlaceholderPage'
import ProvidersPage from './pages/ProvidersPage'
import StatusPage from './pages/StatusPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        {/* 根路径直接进编码助手 —— 别让人看见「尚未实现」 */}
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="chat/:threadId" element={<ChatPage />} />
        <Route path="files" element={<FilesPage />} />
        <Route path="environments" element={<EnvironmentsPage />} />
        <Route path="providers" element={<ProvidersPage />} />
        <Route path="capabilities" element={<CapabilitiesPage />} />
        <Route path="status" element={<StatusPage />} />
        <Route path="*" element={<PlaceholderPage module="页面" stage="未规划" />} />
      </Route>
    </Routes>
  )
}
