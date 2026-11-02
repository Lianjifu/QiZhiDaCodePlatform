# 企智搭·代码平台 —— 前端控制台

React + TypeScript + Vite 的单页应用，后端是仓库里的 `backend/`（FastAPI）。

## 起服务

```bash
cd frontend
npm install
npm run dev
```

打开 <http://localhost:6001>。

**端口 6001**，写死在 `vite.config.ts` 里（`strictPort: true`，端口被占就直接报错，
不静默换端口）。

## 连后端

前端只用相对路径 `/api`，由 **Vite dev server 转发**到后端：

```text
浏览器 → /api/v1/xxx → Vite proxy → http://127.0.0.1:<后端端口>/api/v1/xxx → FastAPI
```

走代理而不是直连的原因：省掉 CORS、SSE 不用特殊处理、生产同域部署时前端代码
一行都不用改。

**后端端口默认取后端 `.env` 里的 `PORT`**（当前是 8000），所以克隆下来两端
都能用默认配置跑起来。

用 `--port` 临时改了后端端口的，前端要跟着设环境变量：

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:8080 npm run dev
```

> ⚠️ **后端必须从 `backend/` 目录启动**。`.env` 在那儿，`Settings` 是按当前工作
> 目录找它的 —— 在仓库根目录启动会读不到配置（API Key 空、数据库连到另一个空库）。
>
> ```bash
> cd backend && ../.venv/bin/uvicorn app.main:app --reload
> ```

## 目录结构

```text
src/
  api/          后端客户端
    client.ts   fetch 封装：统一解包 ResponseModel、归一错误、留鉴权注入口
    sse.ts      SSE 帧解析（攒够一帧再处理）
    resources.ts 各资源的接口，按后端 router 分
  theme/
    tokens.css  语义色 token（浅色 / 深色两套）
    ThemeProvider.tsx  三档主题：跟随系统 / 浅色 / 深色
  types/api.ts  与后端 schema 一一对应的 TS 类型
  components/
    layout/AppShell.tsx    左侧栏 + 主区
    layout/ThemeToggle.tsx
  features/
    agent/         编码助手（唯一支持多轮对话的页面）
      ChatPage.tsx  MessageList.tsx  ToolCallBlock.tsx
      ApprovalCard.tsx  Composer.tsx  useChat.ts
    sessions/
      store.tsx     会话元数据（localStorage）
  pages/
    StatusPage.tsx  FilesPage.tsx  ProvidersPage.tsx  CapabilitiesPage.tsx
  lib/          工具函数
```

## 主题：跟随系统 / 浅色 / 深色

三档，不只是开关 —— 只做「浅/深」两档的话，用户手动设了深色之后，系统切到浅色
他不会跟着变，而「跟随系统」那档能解决。

深浅的切换靠给 `<html>` 挂 `.dark` 类（Tailwind v4 的 `dark:` 变体已从
`prefers-color-scheme` 改成 class 策略）。

**组件里不许写具体色值** —— 一律用语义类（`bg-base` / `text-ink` /
`border-subtle`），具体值只在 `theme/tokens.css` 里改。语义 token 而不是色板：
`slate-500` 只说了颜色，深色下得整体重映射一遍；语义 token 直接表达「这是次要
文字」，深浅两套各给一个值，组件不用动。

## 页面

| 路由 | 内容 |
| --- | --- |
| `/chat` `/chat/:threadId` | **编码助手** —— 唯一支持多轮对话的页面 |
| `/files` | 工作区文件（上传件与产物） |
| `/providers` | 模型接入配置 |
| `/capabilities` | 统一能力清单 |
| `/status` | 前后端联通检查（真实调接口并显示响应体） |

## 聊天页的事件流

后端 `/agent/stream` 推的**一帧一种事件**（`type` 字段决定）：

| 事件 | 前端动作 |
| --- | --- |
| `text` | 追加到最后一条助手消息（打字机） |
| `tool_call` | 加一个工具折叠块（进行中） |
| `tool_result` | 按 tool_call_id 找到那个块，补上结果 |
| `pending_approval` | 弹审批卡片 |
| `done` | 收尾 |

多轮靠 `thread_id` 串 —— 后端用 checkpoint 存历史，前端一直带着同一个 id。
刷新页面后从 `GET /agent/{thread_id}/history` 拉回来。

**会话列表在 localStorage**（标题、置顶）—— 后端没有会话表，这是已知缺口。
消息在 checkpoint 里，清了浏览器数据只是列表没了，消息还在。

## 约定

**类型要跟后端 schema 对齐**。前端没有代码生成，改了后端 schema 要手工同步
`src/types/api.ts`。

**页面不直接调 `api.get`**，走 `src/api/resources.ts` —— 路径与参数收在一处。

**鉴权留了口子**：`client.ts` 的 `setHeaderProvider` 现在返回空对象。平台目前
没有鉴权（后端所有接口都是裸的），将来加登录/令牌只改这一处。

**`/api/v1/health` 返回的是裸对象**，没有 `ResponseModel` 那层 —— 所以
它走 `api.getRaw`，不是 `api.get`。

## 命令

```bash
npm run dev      # 开发服务器（6001）
npm run build    # 类型检查 + 生产构建
npm run preview  # 预览生产构建
npm run lint     # oxlint
```
