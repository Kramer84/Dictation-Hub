#!/bin/bash
# client/init_client.sh

BIN_DIR="$HOME/.local/bin"
CLIENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_LINK="$BIN_DIR/dictate"

echo "========================================"
echo " Initializing Client Global Command"
echo "========================================"

mkdir -p "$BIN_DIR"

if [ -L "$TARGET_LINK" ]; then
    rm "$TARGET_LINK"
fi

ln -s "$CLIENT_DIR/dictate_client.sh" "$TARGET_LINK"
echo "✅ Client symlink created at: $TARGET_LINK"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    echo "✅ Added ~/.local/bin to ~/.bashrc. Please run 'source ~/.bashrc'."
fi

if [ ! -f "$CLIENT_DIR/client.env" ]; then
    cp "$CLIENT_DIR/client.env.template" "$CLIENT_DIR/client.env"
    echo "⚠️  Created client.env. Please edit it to add your main computer's Tailscale IP."
fi