#!/bin/bash
# client/init_client.sh

BIN_DIR="$HOME/.local/bin"
CLIENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_LINK="$BIN_DIR/dictate"

echo "========================================"
echo " Initializing Client Global Command"
echo "========================================"

mkdir -p "$BIN_DIR"

# Clean up local server symlinks to avoid environment pollution
rm -f "$TARGET_LINK"
rm -f "$BIN_DIR/dictate-server-start"
rm -f "$BIN_DIR/dictate-server-stop"
rm -f "$BIN_DIR/dictate-server-setup"

chmod +x "$CLIENT_DIR/dictate_client.sh"
ln -s "$CLIENT_DIR/dictate_client.sh" "$TARGET_LINK"
echo "✅ Client symlink created at: $TARGET_LINK"
echo "✅ Cleaned up any legacy local server symlinks."

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    echo "✅ Added ~/.local/bin to ~/.bashrc. Please run 'source ~/.bashrc'."
fi

if [ ! -f "$CLIENT_DIR/client.env" ]; then
    cp "$CLIENT_DIR/client.env.template" "$CLIENT_DIR/client.env"
    echo "⚠️  Created client.env. Please edit it to add your main computer's Tailscale IP."
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