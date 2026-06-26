#!/bin/bash
# server/setup_server.sh

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SERVER_DIR" || exit 1

echo "-> Creating Python virtual environment..."
python3 -m venv venv

source venv/bin/activate
pip install -r requirements.txt
echo "✅ Server setup complete. Use launch_server.sh to start."