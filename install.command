#!/bin/zsh
set -e

APP_DIR="${0:A:h}"
BIN_DIR="$HOME/.local/bin"
COMMAND_PATH="$BIN_DIR/ai-project-finder"
typeset locale="en"
if /usr/bin/grep -Eq '"locale"[[:space:]]*:[[:space:]]*"zh' "$APP_DIR/config.json" 2>/dev/null; then
  locale="zh-CN"
fi

chmod +x "$APP_DIR/start.command" "$APP_DIR/install.command"
if command -v xattr >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$APP_DIR" 2>/dev/null || true
fi

mkdir -p "$BIN_DIR"
ln -sfn "$APP_DIR/start.command" "$COMMAND_PATH"

if [[ "$locale" == "zh-CN" ]]; then
  echo "已安装本地命令：$COMMAND_PATH"
  echo "正在启动 AI Project Finder..."
else
  echo "Installed command: $COMMAND_PATH"
  echo "Starting AI Project Finder..."
fi
exec "$APP_DIR/start.command"
