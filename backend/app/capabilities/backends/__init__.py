"""能力层的后端适配：把我们的执行环境接到 deepAgents 的 backend 协议上。

deepAgents 的文件工具直连 backend，命令执行也走 backend。backend 换成我们的，
deepAgents 的工具就自动受我们的治理约束（校验 / 授权 / 风险 / 审批 / 留痕）。

后端组装见 :func:`app.capabilities.backends.assembly.build_backend`。
"""

from app.capabilities.backends.assembly import build_backend
from app.capabilities.backends.sandbox_backend import DockerSandbox

__all__ = ["DockerSandbox", "build_backend"]
