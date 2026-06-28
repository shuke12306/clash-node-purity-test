#!/usr/bin/env bash
# macOS / Linux 一键启动器，等价于 Windows 的「一键优选.bat」。
# 切到脚本所在目录后运行 node_purity_tool.py menu，并透传额外参数，例如：
#   ./run.sh --regions 台湾,日本 --report both
set -euo pipefail

# 切到脚本所在目录，保证相对路径（local_config.json / 节点源）解析正确。
cd "$(dirname "$0")"

# 优先 python3，回退 python；都没有则给出明确提示。
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "✗ 未找到 python3 / python，请先安装 Python 3.9+。" >&2
    exit 1
fi

exec "$PY" node_purity_tool.py menu "$@"
