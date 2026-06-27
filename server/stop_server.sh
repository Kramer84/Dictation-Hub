#!/bin/bash
# server/stop_server.sh

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SERVER_DIR/server.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "⚠️  No server.pid found. Attempting aggressive fallback kill..."
    pkill -f "uvicorn main:app" 2>/dev/null
    echo "✅ Fallback cleanup executed."
    exit 0
fi

SERVER_PID=$(cat "$PID_FILE")

if kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "🛑 Sending termination signal to Server (PID: $SERVER_PID)..."
    kill "$SERVER_PID"
    
    # Wait to allow FastAPI to cleanly drop connections
    sleep 2 
    
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "⚠️  Process hung. Sending SIGKILL..."
        kill -9 "$SERVER_PID"
    fi
    echo "✅ Server successfully stopped."
else
    echo "⚠️  Server was not running. Cleaning PID file."
fi

rm -f "$PID_FILE"