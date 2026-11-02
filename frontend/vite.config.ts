import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig, loadEnv } from 'vite'

// 后端地址：开发时前端只用相对路径 /api，由这里转发过去。
//
// **走代理而不是直连**：省掉 CORS，SSE 不用特殊处理，生产同域部署时前端代码
// 一行都不用改。
//
// 默认取后端 .env 里的 PORT（后端自己也在这个端口上跑），所以克隆下来就能用。
// 用 --port 临时改了后端端口的，前端要跟着设 VITE_API_PROXY_TARGET。
const backendEnv = loadEnv('', fileURLToPath(new URL('../backend', import.meta.url)), '')
const backendPort = backendEnv.PORT || '8000'
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? `http://127.0.0.1:${backendPort}`

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 6001,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
