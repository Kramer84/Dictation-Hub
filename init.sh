#!/bin/bash
# init.sh

BIN_DIR="$HOME/.local/bin"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_LINK="$BIN_DIR/dictate"

echo "========================================"
echo " Initializing Global Workspace Command"
echo "========================================"

mkdir -p "$BIN_DIR"

if [ -L "$TARGET_LINK" ]; then
    rm "$TARGET_LINK"
fi

chmod +x ~/SpeechToTextTranscriptionTool/core/*.sh

ln -s "$REPO_ROOT/core/execution_router.sh" "$TARGET_LINK"
echo "✅ Created symlink at: $TARGET_LINK"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    echo "✅ Added ~/.local/bin to ~/.bashrc. Please run 'source ~/.bashrc'."
else
    echo "✅ ~/.local/bin is already in your PATH."
fi