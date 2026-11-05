#!/bin/sh
# 容器入口：先迁移数据库，再起服务。
#
# **为什么在容器里跑迁移而不是靠 create_all** —— `DB_AUTO_CREATE_TABLES` 在开发
# 期是开的（`create_all` 建表），但它**不知道 alembic 版本** —— 生产要能回滚
# 就得有一份真实的迁移链。容器里把它关掉（见 docker-compose.yml），
# 建表这件事交给 alembic。
#
# 迁移**幂等**：重复启动是安全的（alembic 记版本，只跑没跑过的）。
set -e

# 工作区必须是绝对路径 —— 沙箱容器的挂载参数 `-v <路径>:/workspace` 里的
# <路径> 是**宿主机的路径**（docker daemon 解释的）。相对路径会按容器自己的
# 目录解析，挂进去的是个空目录，而且**不报错**。
if [ -n "${WORKSPACE_ROOT:-}" ] && [ "${WORKSPACE_ROOT#/}" = "${WORKSPACE_ROOT}" ]; then
    echo "[entrypoint] WORKSPACE_ROOT 必须是绝对路径（现在：${WORKSPACE_ROOT}）" >&2
    exit 1
fi

echo "[entrypoint] 跑数据库迁移…"
alembic upgrade head

echo "[entrypoint] 起 uvicorn，端口 ${PORT:-8000}"
# `--proxy-headers` 让 uvicorn 认 X-Forwarded-For —— 前端 nginx 转发时会把
# 真实客户端 IP 带过来。`--forwarded-allow-ips='*'` 在这里是安全的：compose 里
# **后端端口不映射到宿主机**，能连到它的只有同一个 docker 网络里的 nginx。
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips='*'
