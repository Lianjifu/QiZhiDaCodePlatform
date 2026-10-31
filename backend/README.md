# QiZhiDa Code Platform — Backend

FastAPI 后端脚手架，采用**简化分层架构（Service Layer）**。

## 分层结构

```text
HTTP 请求
   ↓
api/v1/xxx.py        接口层（Controller）—— 只做参数接收与响应转换
   ↓
services/xxx.py      服务层（Service）—— 业务规则与事务边界，核心层
   ↓
repositories/xxx.py  仓储层（Repository）—— 只做数据存取
   ↓
models/xxx.py        模型层（ORM）
   ↓
db/session.py        引擎与会话
```

横向支撑：

- `core/`：配置、异常、日志
- `schemas/`：Pydantic 请求/响应模型
- `api/deps.py`：依赖注入（DB 会话 → Service 实例）

### 几条硬约定

1. Service 不依赖 FastAPI 的 `Request` / `Response`，需要 HTTP 语义时抛 `core.exceptions.AppError` 子类，由全局异常处理器翻译成 HTTP 响应。
2. 一个 Service 方法 = 一个业务用例 = 一个事务边界（`commit()` 在 Service 里）。
3. Controller 不直接访问数据库。
4. 所有写入接口通过 `schemas/` 做入参校验，通过 `schemas/` 做响应序列化。

### 响应格式

成功：

```json
{ "code": 0, "message": "ok", "data": { "id": 1, "name": "demo" } }
```

失败（由全局异常处理器统一产出）：

```json
{
  "code": 404,
  "error_code": "NOT_FOUND",
  "message": "示例 1 不存在",
  "details": null
}
```

## 目录结构

```text
backend/
├── app/
│   ├── main.py              # 应用工厂 + 全局异常处理 + lifespan
│   ├── core/                # config / exceptions / logging
│   ├── api/
│   │   ├── deps.py          # 依赖注入
│   │   ├── router.py        # 路由聚合
│   │   └── v1/              # 版本化接口
│   │       ├── health.py
│   │       └── example.py
│   ├── schemas/             # 请求 / 响应模型
│   ├── services/            # 业务层
│   ├── repositories/        # 数据访问层
│   ├── models/              # ORM 模型
│   └── db/                  # 引擎与会话
├── scripts/check_syntax.py  # 不装依赖也能跑的语法自检
├── tests/
├── .env.example
├── pyproject.toml
└── requirements.txt
```

## 启动

需要 Python 3.11+（FastAPI 0.16x / SQLAlchemy 2.1 要求 3.11 以上）。
仓库根目录已有 `.venv`（Python 3.13，依赖已装好），直接用它即可：

```bash
cd backend
source ../.venv/bin/activate
cp .env.example .env

uvicorn app.main:app --reload
# 或 python3 -m app.main
```

自己新建环境的话：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

想让 `app` 包在仓库根目录也能直接导入（不必 `cd backend`），把 backend 以可编辑模式装进环境：

```bash
.venv/bin/pip install -e ./backend
```

- Swagger：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/v1/health>

## 测试

```bash
cd backend
source ../.venv/bin/activate
pytest -q
```

只想快速检查语法（不装依赖也能跑）：

```bash
python3 scripts/check_syntax.py
```

## 新增一个业务模块的顺序

1. `models/` 加 ORM 模型
2. `schemas/` 加请求/响应模型
3. `repositories/` 加仓储（继承 `BaseRepository`）
4. `services/` 加服务（继承 `BaseService`），写业务规则
5. `api/v1/` 加接口，依赖用 `app/api/deps.py` 里的 `Depends`
6. 在 `app/api/router.py` 挂载路由
