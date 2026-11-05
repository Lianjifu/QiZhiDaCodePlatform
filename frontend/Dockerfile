# 前端镜像：构建静态资源 → nginx 托管。
#
# 为什么不让 FastAPI 托管：前端是 SPA（react-router），刷新子路由要回
# index.html；后端是 API，同域托管两件事会让路由规则互相干扰。
# nginx 各管一段更干净。

# --- 构建 ---
FROM node:24-alpine AS build

WORKDIR /app

# 依赖单独一层 —— 改代码不用重装依赖
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# --- 托管 ---
FROM nginx:1.29-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
