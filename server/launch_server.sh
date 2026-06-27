#!/bin/bash
# server/launch_server.sh

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SERVER_DIR" || exit 1

if [ ! -d "venv" ]; then
    echo "❌ Error: Virtual environment not found. Run setup_server.sh first."
    exit 1
fi

TS_IP=$(tailscale ip -4 2>/dev/null)

if [ -z "$TS_IP" ]; then
    echo "❌ Error: Could not determine Tailscale IP. Is Tailscale running?"
    exit 1
fi

source venv/bin/activate

echo "🔒 Starting Server securely locked to Tailscale interface: $TS_IP"
exec uvicorn main:app --host "$TS_IP" --port 8000