#!/bin/zsh
set -e

cd "${0:A:h}"

typeset locale="en"
if /usr/bin/grep -Eq '"locale"[[:space:]]*:[[:space:]]*"zh' config.json 2>/dev/null; then
  locale="zh-CN"
fi

typeset -a candidates
candidates=(
  "${AI_PROJECT_FINDER_PYTHON:-}"
  "/opt/homebrew/bin/python3"
  "/usr/local/bin/python3"
  "/usr/bin/python3"
)

for candidate in "${candidates[@]}"; do
  if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
    exec "$candidate" app.py --open
  fi
done

if command -v python3 >/dev/null 2>&1; then
  candidate="$(command -v python3)"
  if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
    exec "$candidate" app.py --open
  fi
fi

if [[ "$locale" == "zh-CN" ]]; then
  echo "AI Project Finder 需要 Python 3.10 或更高版本。"
  echo "可以从 https://www.python.org/downloads/macos/ 安装，然后重新运行此文件。"
  read -k 1 "?按任意键关闭。"
else
  echo "AI Project Finder needs Python 3.10 or newer."
  echo "Install it from https://www.python.org/downloads/macos/ and run this file again."
  read -k 1 "?Press any key to close."
fi
