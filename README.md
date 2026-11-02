# 企智搭·代码平台

代码 agent 平台。前端是 React 控制台，后端是 FastAPI —— 两者在同一个仓库里。

```text
QiZhiDaCodePlatform/
├── backend/     FastAPI 后端：agent（deepAgents）、能力层、记忆、RAG、文件存储
└── frontend/    React 控制台：Vite + React + TypeScript + Tailwind
```

## 快速开始

要两个都起来：

```bash
# 1. 后端（**必须在 backend/ 目录启动** —— .env 在那儿）
cd backend
../.venv/bin/uvicorn app.main:app --reload
# Swagger: http://localhost:8000/docs

# 2. 前端
cd frontend
npm install
npm run dev
# 控制台: http://localhost:6001
```

前端通过 Vite 代理把 `/api` 转发到后端，所以**不用配 CORS**，生产同域部署时
前端代码一行都不用改。代理目标自动取后端 `.env` 里的 `PORT`。

## 前端

React + TypeScript + Vite + Tailwind，深浅两套主题（跟随系统 / 浅色 / 深色）。

核心是**编码助手**页面 —— 唯一支持多轮对话的页面：消息流式渲染、工具调用折叠成
`Ran npm install…` 那种块、审批卡片直接弹在对话里。旁边的文件、能力清单、模型
接入配置是查看与配置页。

细节见 [frontend/README.md](frontend/README.md)。

## 文档

| | |
| --- | --- |
| [backend/README.md](backend/README.md) | 后端：分层、模块、接口、配置 |
| [backend/docs/README.md](backend/docs/README.md) | 后端文档索引（已实现 / 待做分开） |
| [frontend/README.md](frontend/README.md) | 前端：起服务、目录结构、阶段划分 |

## 两条要记住的坑

**一、后端必须从 `backend/` 启动。** `.env` 在那个目录，`Settings` 按当前工作
目录找它 —— 在仓库根目录启动会读不到配置：API Key 空、数据库连到另一个空库、
工作区也落到别处。看到启动时那条「未配置 LLM_API_KEY」的警告，就是踩了这个。

**二、平台目前没有鉴权。** 所有接口都是裸的：模型接入配置、能力调用、审批、
记忆、文件。**文件接口这条最重** —— 知道 `thread_id` 就能下载别人会话里的所有
文件。上线前必须补。
