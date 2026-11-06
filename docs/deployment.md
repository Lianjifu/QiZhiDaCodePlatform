# Docker 部署

用 Docker 起这套平台要的东西。**两个容器**：

```text
浏览器 → frontend (nginx, 80)  ──/api──→  backend (FastAPI, 8000)
                                        │
                                        └──docker.sock──→ 沙箱容器（兄弟容器）
```

后端端口**不映射到宿主机** —— 只有一个入口，少一个暴露面（平台目前没有鉴权）。

---

## 1. 快速开始

```bash
# 1. 准备环境变量（仓库根目录，不是 backend/）
cp .env.docker.example .env

# 2. 填两样必填的
#    WORKSPACE_ROOT —— 工作区根目录，**绝对路径**，见 §3
#    API_KEY_ENCRYPTION_KEY —— 生成一把：
#        cd backend && ../.venv/bin/python scripts/gen_encryption_key.py

# 3. 起
docker compose up -d --build

# 4. 看
docker compose logs -f backend
```

控制台在 <http://localhost:8080>，接口文档在 <http://localhost:8080/docs>。

**验一下部署**（健康检查 + 前端 + 能力清单 + 沙箱真能跑命令 + 工作区是共享的）：

```bash
cd backend
../.venv/bin/python scripts/deploy_smoke.py \
    --base-url http://localhost:8080 \
    --workspace-root "$WORKSPACE_ROOT"
```

停：`docker compose down`（数据卷留着）；连数据一起清：`docker compose down -v`。

---

## 2. 两份 .env 不是一回事

| 文件 | 给谁用 | 什么时候 |
| --- | --- | --- |
| `backend/.env` | 本地起后端（`uvicorn`） | 本地开发 |
| **仓库根的 `.env`** | compose 插值 + 注入容器 | Docker 部署 |

根目录那个 `.env` 一个文件两用：compose 拿它做 `${WORKSPACE_ROOT}` 这类插值，
同时把里面的键全部注入容器（`env_file`）。已被 `.gitignore` 忽略。

**为什么不让后端读 `backend/.env`** —— 容器里 `Settings` 按**当前工作目录**找
`.env`（`/app/.env`），那是开发机上的路径与配置；部署时该由 compose 注入，
而不是把开发机的配置烤进镜像。

---

## 3. 工作区的路径必须一致 ⚠️

**宿主与容器里，工作区必须是同一个路径字符串。**

compose 里这么写：

```yaml
- ${WORKSPACE_ROOT}:${WORKSPACE_ROOT}
```

为什么这么讲究 —— agent 跑命令的**沙箱容器**是这台机器的兄弟容器，平台用

```python
docker run -v <工作区>:/workspace ...
```

建它们。那个 `<工作区>` 是**宿主机的路径**（docker daemon 解释的），不是容器里的。
两处路径字面不同的话：

- 挂进去的是**空目录**（macOS 上尤其隐蔽：docker 不报错，只是挂个空的）；
- 平台的挂载校验 `Path(挂载源).resolve() == workspace` 判成"挂错了"，
  于是**每次执行命令都重建容器**。

所以 `WORKSPACE_ROOT` 必须是绝对路径，且宿主与容器里一致。

**macOS 上还要放在 `$HOME` 下面** —— Docker Desktop / Colima 只共享 `$HOME`，
放别处同样不报错，只是挂空目录。

---

## 4. docker socket 与安全边界 ⚠️

compose 挂了 `/var/run/docker.sock`：

```yaml
- /var/run/docker.sock:/var/run/docker.sock
```

沙箱容器是**兄弟容器**，不是嵌套容器 —— 平台直接跟宿主机的 docker daemon 说话。
没这个 socket 就建不出沙箱。

**代价**：挂了 socket 等于把宿主机的 root 权限交给了容器。平台没有鉴权，
agent 又能执行命令，所以这套部署**只适合可信机器**，别直接暴露到公网。

沙箱容器本身是隔离的（`--network none --cap-drop ALL --no-new-privileges`，
限内存限 CPU 限进程），但那只管**沙箱里**跑的东西，管不了"平台本身被攻破"。

---

## 5. 数据落在哪

| 数据 | 落在 | 说明 |
| --- | --- | --- |
| 数据库（`app.db`） | 命名卷 `qzd-data` → `/data/app.db` | SQLite。备份要把卷挂到另一个容器里 `sqlite3 .dump`，不能直接拷宿主上的文件 |
| checkpoint（`agent_checkpoints.db`） | 同一个卷 → `/data/` | 同上 |
| 工作区文件 | 宿主目录 `${WORKSPACE_ROOT}` | 跟沙箱容器共享同一份 |

备份大概长这样：

```bash
docker run --rm -v qizhidacode_qzd-data:/data alpine \
    sh -c 'cd /data && sqlite3 app.db ".dump" > /backup/app.sql'
```

**建表靠 alembic，不靠 `create_all`** —— compose 里显式设了
`DB_AUTO_CREATE_TABLES=false`，入口脚本先跑 `alembic upgrade head` 再起服务。
`create_all` 不知道迁移版本，回滚不了。

已有的库要纳管（之前是 `create_all` 建的、没有 `alembic_version`）：

```bash
docker compose run --rm backend alembic stamp head
```

`stamp` 只是**标记版本**、不跑迁移 —— 前提是那个库的结构**已经**是 head 的。
不是的话得先比对结构再决定是 `stamp` 到某个版本还是重建。

---

## 6. 升级

```bash
git pull
docker compose up -d --build backend
```

入口脚本会自己跑迁移。**回滚代码**要连带回滚迁移 —— 迁移是单向的，
没有自动的 downgrade 步骤。

---

## 7. 常见问题

**页面能开，接口全 502**
后端没起来或还在跑迁移。`docker compose logs backend` 看。多半是迁移失败 ——
最常见的是 `API_KEY_ENCRYPTION_KEY` 没配（不影响迁移，但影响存模型密钥）。

**对话的回复不是一条条出来的，而是卡半天整段出现**
nginx 的缓冲没关。`frontend/nginx.conf` 里 `proxy_buffering off` 就在
`location /api/` 里 —— 改坏了就会出现这个症状。

**agent 跑命令说"工作区是空的"，或者每次执行都重建容器**
路径不一致。见 §3。

**macOS 上工作区里看不到文件**
`$HOME` 之外的工作区不会被共享。见 §3。

**沙箱容器起不来**
后端容器里没有 docker CLI，或者 socket 没挂上。
`docker compose exec backend docker ps` 看得到吗？

**对话报 "Initializing ChatDeepSeek requires the langchain-deepseek package"**
`.env` 里的 `LLM_PROVIDER` 写成了 `deepseek`，而平台**没按预设映射**它 ——
`init_chat_model("deepseek:xxx")` 会去找没装的 `langchain-deepseek` 包。

现在 `LLM_PROVIDER` **直接写厂商名就行**（`deepseek` / `qwen` / `moonshot` /
`openai` / `anthropic` / `ollama` …），平台自动映射成 OpenAI 兼容端点并带上
该厂商的默认地址与上下文窗口，**不用自己拼 `base_url`，也不用装任何厂商
集成包**。确认一下镜像里的 `app/agents/llm.py` 是新版（`get_preset` 映射那一段）。

**前端页面打开了，但接口报 502**
后端容器被重建后 IP 变了，而 nginx 还指着旧的。本平台的 nginx 配了
`server backend:8000 resolve`（按 DNS 现查），正常会自动跟上 ——
如果 502 不消失，看 `docker compose logs frontend` 里有没有
"backend could not be resolved"。

---

## 8. 与本地开发的差异

| | 本地 | Docker |
| --- | --- | --- |
| 后端 | `uvicorn --reload` | 容器里的 uvicorn（无热重载） |
| 数据库 | `backend/app.db` | 命名卷 `/data/app.db` |
| 前端 | vite dev server（6001），代理 /api | nginx（8080），反代 /api |
| 工作区 | 宿主目录 | 宿主目录（同路径挂进容器） |
| 沙箱容器 | 兄弟容器 | **还是**兄弟容器（不是嵌套） |
| 改代码 | 自动重载 | 重建镜像 |

**CORS 在 Docker 下用不到** —— nginx 与前端同域，`/api` 是相对路径。

---

## 9. 相关文件

```text
docker-compose.yml           两个服务：backend + frontend
.env.docker.example          环境变量模板（cp 成 .env）
backend/Dockerfile           后端镜像（docker CLI 从官方镜像拷静态二进制）
backend/docker-entrypoint.sh 跑迁移 → 起 uvicorn
backend/.dockerignore        不进镜像的东西
frontend/Dockerfile          前端镜像（node 构建 → nginx 托管）
frontend/nginx.conf          静态托管 + /api 反代（SSE 关缓冲）
frontend/.dockerignore
```
