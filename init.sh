#!/bin/bash
# init.sh

# --- Zero Verbosity Check ---
# Checks if $SILENT is true, OR if the first argument is -s or --silent
if [[ "$SILENT" == "true" || "$1" == "-s" || "$1" == "--silent" ]]; then
    exec > /dev/null 2>&1  # Redirects both stdout and stderr to the void
fi

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

# Check if the export line already exists inside the file itself
if ! grep -qF 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc"; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    echo "✅ Added ~/.local/bin to ~/.bashrc. Please run 'source ~/.bashrc'."
else
    echo "✅ ~/.local/bin configuration is already present in your ~/.bashrc."
fi
