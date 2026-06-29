#!/bin/bash
# init.sh

if [[ "$SILENT" == "true" || "$1" == "-s" || "$1" == "--silent" ]]; then
    exec > /dev/null 2>&1
fi

BIN_DIR="$HOME/.local/bin"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET_DICTATE="$BIN_DIR/dictate"
TARGET_START="$BIN_DIR/dictate-server-start"
TARGET_STOP="$BIN_DIR/dictate-server-stop"
TARGET_SETUP="$BIN_DIR/dictate-server-setup"

echo "========================================"
echo " Initializing Global Workspace Commands"
echo "========================================"

mkdir -p "$BIN_DIR"

chmod +x "$REPO_ROOT/core/"*.sh
chmod +x "$REPO_ROOT/server/"*.sh

# Overwrite existing symlinks gracefully
ln -sf "$REPO_ROOT/core/execution_router.sh" "$TARGET_DICTATE"
ln -sf "$REPO_ROOT/server/start_server.sh" "$TARGET_START"
ln -sf "$REPO_ROOT/server/stop_server.sh" "$TARGET_STOP"
ln -sf "$REPO_ROOT/server/setup_server.sh" "$TARGET_SETUP"

echo "✅ Created symlink: $TARGET_DICTATE"
echo "✅ Created symlink: $TARGET_START"
echo "✅ Created symlink: $TARGET_STOP"
echo "✅ Created symlink: $TARGET_SETUP"

if ! grep -qF 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc"; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    echo "✅ Added ~/.local/bin to ~/.bashrc. Please run 'source ~/.bashrc'."
else
    echo "✅ ~/.local/bin configuration is already present in your ~/.bashrc."
fi

# --- Autocompletion Setup ---
COMPLETION_SCRIPT="$REPO_ROOT/core/dictate_completion.sh"
chmod +x "$COMPLETION_SCRIPT"

if ! grep -qF "source \"$COMPLETION_SCRIPT\"" "$HOME/.bashrc"; then
    echo "source \"$COMPLETION_SCRIPT\"" >> "$HOME/.bashrc"
    echo "✅ Added dictate autocompletion to ~/.bashrc."
else
    echo "✅ Autocompletion is already configured in ~/.bashrc."
fi