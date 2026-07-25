#!/bin/zsh
set -e

APP_DIR="${0:A:h}"
BIN_DIR="$HOME/.local/bin"
COMMAND_PATH="$BIN_DIR/ai-project-finder"

chmod +x "$APP_DIR/start.command" "$APP_DIR/install.command"
if command -v xattr >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$APP_DIR" 2>/dev/null || true
fi

mkdir -p "$BIN_DIR"
ln -sfn "$APP_DIR/start.command" "$COMMAND_PATH"

echo "Installed command: $COMMAND_PATH"
echo "Starting AI Project Finder..."
exec "$APP_DIR/start.command"
