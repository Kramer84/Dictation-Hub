#!/bin/bash
# server/stop_server.sh

SERVER_DIR="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" >/dev/null 2>&1 && pwd)"
PID_FILE="$SERVER_DIR/server.pid"

# --- 1. Stop LanguageTool Daemon ---
LT_CACHE_DIR="$HOME/.cache/language_tool_python"
LT_JAR=$(find "$LT_CACHE_DIR" -name "languagetool-server.jar" | head -n 1)

if [ -n "$LT_JAR" ]; then
    LT_PID_FILE="$(dirname "$LT_JAR")/server.pid"
    if [ -f "$LT_PID_FILE" ]; then
        LT_PID=$(cat "$LT_PID_FILE")
        if kill -0 "$LT_PID" 2>/dev/null; then
            echo "🛑 Sending termination signal to LanguageTool Daemon (PID: $LT_PID)..."
            kill "$LT_PID"
        fi
        rm -f "$LT_PID_FILE"
    fi
fi

# --- 2. Stop FastAPI Server ---
if [ ! -f "$PID_FILE" ]; then
    echo "⚠️  No server.pid found. Attempting aggressive fallback kill..."
    pkill -f "uvicorn main:app" 2>/dev/null
    echo "✅ Fallback cleanup executed."
    exit 0
fi

SERVER_PID=$(cat "$PID_FILE")

if kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "🛑 Sending termination signal to FastAPI Server (PID: $SERVER_PID)..."
    kill "$SERVER_PID"
    
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