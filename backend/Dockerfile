# 后端镜像。
#
# 关键约束：**沙箱容器**（agent 跑命令的地方）是这台机器的**兄弟容器** ——
# 平台用 `docker run -v <工作区>:/workspace` 建它们。那个 `<工作区>` 路径是
# **宿主机的路径**（docker daemon 解释的），所以工作区在宿主与本容器里
# **必须是同一个路径字符串**，否则挂进去的是空目录（macOS 上尤其隐蔽：
# docker 不报错，只是挂个空目录）。
#
# 这个约束由 compose 负责（同一路径 bind mount 两次），见 docker-compose.yml。

# --- docker CLI：静态二进制，从官方镜像拷过来 ---
# 不用 apt 装 docker.io —— 那会连 daemon 一起装进来，我们只要客户端。
FROM docker:cli AS docker-cli

FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

# curl 供健康检查用；ca-certificates 供 httpx 访问 https 端点
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 沙箱容器的前提：平台要拿 docker CLI 去建/复用兄弟容器
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app

# 依赖单独一层 —— 改代码不用重装依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/v1/health" || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
