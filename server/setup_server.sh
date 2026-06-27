#!/bin/bash
# server/setup_server.sh

# Bulletproof path resolution via realpath
SERVER_DIR="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" >/dev/null 2>&1 && pwd)"
cd "$SERVER_DIR" || exit 1

echo "-> Creating Python virtual environment in $SERVER_DIR..."
python3 -m venv venv

source venv/bin/activate

if [ ! -f "requirements.txt" ]; then
    echo "❌ Error: requirements.txt not found in $SERVER_DIR"
    exit 1
fi

pip install -r requirements.txt
echo "✅ Server setup complete. Use dictate-server-start to launch."