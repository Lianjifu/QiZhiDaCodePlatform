"""部署冒烟测试：对着**已部署**的实例跑一遍关键路径。

跑法：
    python scripts/deploy_smoke.py --base-url http://localhost:8080

它验的是"部署起来之后还能不能用"，不是"代码逻辑对不对"（那由 pytest 管）。
四件事：

1. 健康检查能通（经 nginx —— 说明反代配好了）
2. 前端静态资源能取到（说明构建产物与 nginx 的 SPA 回退配好了）
3. 能力清单能列出
4. **沙箱真能跑命令** —— 调 ``run_python``，落审批、批准，然后验证：
   - 命令确实在**兄弟容器**里跑起来了；
   - 它写出的文件在**宿主的工作区**里看得见。

第 4 条是整套 Docker 部署最容易坏的地方：工作区路径不一致时，docker
**不报错**，只是挂个空目录进去 —— 那时 agent 读文件读到的是"文件不存在"，
而命令还能跑。

工作区根目录用 ``--workspace-root`` 给（默认读环境变量 ``WORKSPACE_ROOT``）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

MARKER = "deploy-smoke-marker.txt"

# 在沙箱里跑的代码：写一个标记文件，并把当前目录列出来
_CODE = f"""
import os
import pathlib

p = pathlib.Path({MARKER!r})
p.write_text("hello from the sandbox", encoding="utf-8")
print("MARKER_PATH", p.resolve())
print("CWD", os.getcwd())
print("ENTRIES", ",".join(sorted(x.name for x in pathlib.Path(".").iterdir())[:20]))
"""


def _fail(message: str) -> None:
    print(f"✗ {message}")
    raise SystemExit(1)


def _ok(message: str) -> None:
    print(f"✓ {message}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--workspace-root", default=os.environ.get("WORKSPACE_ROOT", ""))
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    with httpx.Client(base_url=base, timeout=120.0) as client:
        # 1. 健康检查
        resp = client.get("/api/v1/health")
        if resp.status_code != 200:
            _fail(f"健康检查返回 {resp.status_code}：{resp.text[:200]}")
        payload = resp.json()
        # 这个端点返的是裸 dict（没套 ResponseModel），但别写死 ——
        # 两种形状都认，包装变了也不至于误报。
        body = payload.get("data", payload)
        if body.get("status") != "ok":
            _fail(f"健康检查内容不对：{payload}")
        _ok(f"健康检查通过（版本 {body.get('version')}）")

        # 2. 前端静态资源
        resp = client.get("/")
        if resp.status_code != 200 or "<div id=\"root\"" not in resp.text:
            _fail(f"前端首页不对（{resp.status_code}）：{resp.text[:200]}")
        _ok("前端首页能取到")

        # 3. 能力清单
        resp = client.get("/api/v1/capabilities")
        if resp.status_code != 200:
            _fail(f"能力清单返回 {resp.status_code}：{resp.text[:200]}")
        keys = {item["key"] for item in resp.json().get("data", {}).get("items", [])}
        if "run_python" not in keys:
            _fail(f"能力清单里没有 run_python：{sorted(keys)[:20]}")
        _ok(f"能力清单能列出（{len(keys)} 项）")

        # 4. 沙箱执行 —— 这是整套部署最关键的一步
        resp = client.post("/api/v1/capabilities/run_python/invoke", json={"code": _CODE})
        if resp.status_code not in (200, 202):
            _fail(f"调用 run_python 返回 {resp.status_code}：{resp.text[:300]}")
        data = resp.json().get("data", {})
        invocation_id = data.get("invocation_id")

        if resp.status_code == 202 or data.get("status") == "pending":
            if not invocation_id:
                _fail(f"挂起了但没给调用记录 id：{data}")
            _ok(f"run_python 落审批（调用记录 {invocation_id}）")
            resp = client.post(f"/api/v1/invocations/{invocation_id}/approve")
            if resp.status_code != 200:
                _fail(f"批准返回 {resp.status_code}：{resp.text[:300]}")
            data = resp.json().get("data", {})

        # 结果有两种形状：结构化的（带 output / exit_code）与扁平的
        # （只有渲染好的 content，形如 "退出码: 0\n<输出>"）。两种都认。
        result = data.get("result") or data
        text = "\n".join(
            str(result.get(key) or "") for key in ("output", "content")
        )
        if not result.get("ok"):
            _fail(f"沙箱执行不成功：{json.dumps(result, ensure_ascii=False)[:400]}")
        if "MARKER_PATH" not in text:
            _fail(f"沙箱输出里没有标记：{text[:400]}")
        _ok("命令在沙箱容器里跑起来了")

        # 5. 工作区共享 —— 文件要能在宿主的工作区里看见
        if not args.workspace_root:
            _fail("没给 --workspace-root，跳过工作区校验")
        marker = Path(args.workspace_root) / MARKER
        if not marker.exists():
            _fail(f"沙箱写的文件在宿主工作区里看不见：{marker} 不存在")
        if marker.read_text(encoding="utf-8").strip() != "hello from the sandbox":
            _fail(f"标记文件内容不对：{marker.read_text(encoding='utf-8')[:100]}")
        _ok(f"工作区是共享的（{marker}）")
        marker.unlink(missing_ok=True)

    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
