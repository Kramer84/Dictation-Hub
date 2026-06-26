#!/bin/bash
# server/init_server.sh

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SERVER_DIR" || exit 1

if [ ! -d "venv" ]; then
    echo "-> Creating Python virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -r requirements.txt

echo "-> Starting FastAPI Transcription Server on Tailscale Port 8000..."
# Uses 0.0.0.0 to ensure it listens across all interfaces, including the Tailscale NIC.
uvicorn main:app --host 0.0.0.0 --port 8000