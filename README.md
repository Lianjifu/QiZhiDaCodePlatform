# 企智搭·代码平台

定位: 面向企业研发团队的AI编程与工程协作平台,覆盖代码生成、评审、部署全流程

代码 agent 平台。前端是 React 控制台，后端是 FastAPI —— 两者在同一个仓库里。

**现在到哪一步**：agent 能在会话绑定的代码库里读代码、跑命令、改代码、提
commit、开分支并合并；改不动的地方会挂起等人批，调用记录留痕可查。
功能清单见 [docs/README.md](docs/README.md)。

```text
QiZhiDaCodePlatform/
├── backend/     FastAPI 后端：agent（deepAgents）、能力层、记忆、RAG、文件存储
├── frontend/    React 控制台：Vite + React + TypeScript + Tailwind
├── docs/        按功能模块组织的文档 + 部署说明
└── docker-compose.yml   Docker 部署：nginx（前端）+ FastAPI（后端）
```

## 快速开始

要两个都起来：

```bash
# 1. 后端（**必须在 backend/ 目录启动** —— .env 在那儿）
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
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

后端的配置在 `backend/.env`（照着 `backend/.env.example` 改）—— 模型密钥、
数据库、工作区都在那儿。**平台无鉴权，别把它暴露到公网。**

## Docker 部署

```bash
cp .env.docker.example .env    # 填 WORKSPACE_ROOT 与 API_KEY_ENCRYPTION_KEY
docker compose up -d --build
```

控制台在 <http://localhost:8080>。两个容器：nginx 托管前端并反代 `/api`，
FastAPI 跑 API 与 agent。验一下（健康检查 / 前端 / 能力清单 / 沙箱真能跑命令 /
工作区是共享的）：

```bash
cd backend && ../.venv/bin/python scripts/deploy_smoke.py \
    --base-url http://localhost:8080 --workspace-root /你的/工作区路径
```

**工作区路径必须宿主与容器一致**（沙箱容器的挂载参数按宿主路径解释），
**后端要挂 docker socket**（沙箱是兄弟容器）—— 两条细节见
[docs/deployment.md](docs/deployment.md)。

## 前端

React + TypeScript + Vite + Tailwind，深浅两套主题（跟随系统 / 浅色 / 深色）。

核心是**编码助手**页面 —— 唯一支持多轮对话的页面：消息流式渲染、工具调用折叠成
`Ran npm install…` 那种块、审批卡片直接弹在对话里。旁边的文件、能力清单、模型
接入配置是查看与配置页。

细节见 [frontend/README.md](frontend/README.md)。

## 文档

按**功能模块**组织 —— 一篇讲清一个模块：它是什么、接口有哪些、怎么实现的、
哪些地方改错了会出事、现在还差什么。

| | |
| --- | --- |
| [docs/README.md](docs/README.md) | **功能模块文档索引**（编码助手 / 能力 / 记忆 / 知识库 / 上下文 / 文件 / 工作目录 / 模型接入 / 沙箱 / 自我进化） |
| [docs/deployment.md](docs/deployment.md) | Docker 部署：两个容器、工作区路径的硬约束、docker socket 的安全边界、常见问题 |
| [backend/README.md](backend/README.md) | 后端：分层、目录、接口、配置、迁移、风险 |
| [frontend/README.md](frontend/README.md) | 前端：起服务、目录结构、主题、约定 |

## 三条要记住的坑

**一、后端必须从 `backend/` 启动。** `.env` 在那个目录，`Settings` 按当前工作
目录找它 —— 在仓库根目录启动会读不到配置：API Key 空、数据库连到另一个空库、
工作区也落到别处。看到启动时那条「未配置 LLM_API_KEY」的警告，就是踩了这个。

**二、平台目前没有鉴权。** 所有接口都是裸的：模型接入配置、能力调用、审批、
记忆、文件。**文件接口这条最重** —— 知道 `thread_id` 就能下载别人会话里的所有
文件。上线前必须补。

**三、工作区路径不能乱填。** agent 跑命令的沙箱容器是**兄弟容器**，它的挂载
参数按**宿主的路径**解释 —— 工作区在宿主与容器里必须是同一个路径，否则挂进去
的是空目录，而且 docker **不报错**（agent 只看到"文件不存在"）。macOS 上工作区
还得在 `$HOME` 下面 —— Docker Desktop / Colima 只共享 `$HOME`。
