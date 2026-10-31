"""开发期自检脚本：解析后端全部 Python 文件，检查语法错误。

用法：python3 scripts/check_syntax.py
"""

import ast
import glob
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    files = sorted(glob.glob(os.path.join(BACKEND_DIR, "**", "*.py"), recursive=True))
    errors: list[str] = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                ast.parse(fh.read(), filename=path)
        except SyntaxError as exc:
            errors.append(f"{path}: {exc}")

    print(f"已解析 {len(files)} 个文件")
    for err in errors:
        print("语法错误:", err)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
