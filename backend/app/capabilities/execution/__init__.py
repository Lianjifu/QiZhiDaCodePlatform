"""执行层：所有能力调用的必经之路。

七个阶段：schema 校验 → 权限校验 → 风险评估 → 审批等待 → 实际调用 → 结果验证
→ 结果回写。顺序刻意如此：便宜的确定性检查在前，昂贵的人介入在中，副作用在后。

这个 ``__init__`` 只做最小导出：注册层的内置能力会 import
``execution.context``，而 context 不该反过来把执行流水线拖进来（否则注册层
就依赖上了整条执行链路）。流水线与工具构造请直接 import 对应模块。
"""

from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.result import ExecutionResult

__all__ = ["ExecutionContext", "ExecutionResult"]
