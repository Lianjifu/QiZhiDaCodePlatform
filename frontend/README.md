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
> cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8080
> ```

## 目录结构

```text
src/
  api/          后端客户端
    client.ts   fetch 封装：统一解包 ResponseModel、归一错误、留鉴权注入口
    sse.ts      SSE 帧解析（攒够一帧再处理）
    resources.ts 各资源的接口，按后端 router 分
  types/api.ts  与后端 schema 一一对应的 TS 类型
  components/   通用组件（layout/ 是外壳）
  pages/        路由页面
  lib/          工具函数
```

## 已实现 / 待做

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 0 | 骨架、外壳、路由、API 层、联通验证页 | ✅ |
| 1 | 编码助手对话：会话、消息流、工具调用、审批、附件 | ⬜ |
| 2 | 文件工作区：上传 / 列表 / 下载 / 删除 | ⬜ |
| 3 | 配置页：供应商 / 技能 / 工具 / MCP / 能力清单 | ⬜ |
| 4 | 运维页：记忆 / RAG / 调用记录 | ⬜ |

「系统状态」页（`/`）会**真实调用**后端接口并把响应体原样显示出来 —— 前后端
联通、路径写错、响应结构变了，这一页立刻能看出来。

## 约定

**类型要跟后端 schema 对齐**。前端没有代码生成，改了后端 schema 要手工同步
`src/types/api.ts`。

**页面不直接调 `api.get`**，走 `src/api/resources.ts` —— 路径与参数收在一处。

**鉴权留了口子**：`client.ts` 的 `setHeaderProvider` 现在返回空对象。平台目前
没有鉴权（后端所有接口都是裸的），将来加登录/令牌只改这一处。

**`/api/v1/health` 返回的是裸对象**，没有 `ResponseModel` 那层 `data` —— 所以
它走 `api.getRaw`，不是 `api.get`。

## 命令

```bash
npm run dev      # 开发服务器（6001）
npm run build    # 类型检查 + 生产构建
npm run preview  # 预览生产构建
npm run lint     # oxlint
```
