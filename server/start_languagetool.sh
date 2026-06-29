#!/bin/bash
# server/start_languagetool.sh

# LanguageTool caches to this directory by default on Linux
LT_CACHE_DIR="$HOME/.cache/language_tool_python"
LT_JAR=$(find "$LT_CACHE_DIR" -name "languagetool-server.jar" | head -n 1)

if [ -z "$LT_JAR" ]; then
    echo "❌ LanguageTool offline server not found in $LT_CACHE_DIR."
    echo "Run the python script once normally to download it."
    exit 1
fi

PID_FILE="$(dirname "$LT_JAR")/server.pid"

if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
    echo "⚠️  LanguageTool Server is already running (PID: $(cat "$PID_FILE"))."
    exit 0
fi

echo "🚀 Booting Local LanguageTool Server on port 8081..."
nohup java -cp "$LT_JAR" org.languagetool.server.HTTPServer --port 8081 --allow-origin "*" > "$(dirname "$LT_JAR")/server.log" 2>&1 &
LT_PID=$!

echo $LT_PID > "$PID_FILE"
echo "✅ LanguageTool daemonized with PID: $LT_PID"