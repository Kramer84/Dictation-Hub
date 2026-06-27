#!/bin/bash
# server/start_server.sh

SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SERVER_DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"

PID_FILE="$SERVER_DIR/server.pid"

if [ -f "$PID_FILE" ]; then
    if kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "⚠️  Server is already running in the background (PID: $(cat "$PID_FILE"))."
        exit 1
    else
        echo "🧹 Cleaning up stale PID file."
        rm "$PID_FILE"
    fi
fi

echo "🚀 Booting FastAPI Transcription Server in the background..."

nohup bash "$SERVER_DIR/launch_server.sh" > "$SERVER_DIR/server.log" 2>&1 &
SERVER_PID=$!

echo $SERVER_PID > "$PID_FILE"
echo "✅ Server daemonized with PID: $SERVER_PID"
echo "📄 Real-time logs available at: $SERVER_DIR/server.log"