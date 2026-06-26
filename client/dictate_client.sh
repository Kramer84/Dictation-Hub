#!/bin/bash
# client/dictate_client.sh

SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"

ENV_FILE="$SCRIPT_DIR/client.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE not found."
    exit 1
fi

source "$ENV_FILE"

if ! command -v jq &> /dev/null; then
    echo "Error: jq is required."
    exit 1
fi

TEMP_FIFO="/tmp/dictation_fifo_$$"
TEMP_RESP="/tmp/dictation_resp_$$"
mkfifo "$TEMP_FIFO"

echo "🎙️ Recording and streaming... Press [Enter] or [Ctrl+C] to stop."

# Stream from the FIFO using chunked transfer encoding via stdin
curl -s -X POST -H "Transfer-Encoding: chunked" --data-binary @- "http://$SERVER_IP:$SERVER_PORT/transcribe" < "$TEMP_FIFO" > "$TEMP_RESP" &
CURL_PID=$!

# Record raw PCM data instead of WAV to prevent broken header issues
if [ "$AUDIO_BACKEND" == "arecord" ]; then
    arecord -f S16_LE -c 1 -r 16000 -t raw > "$TEMP_FIFO" 2>/dev/null &
else
    rec -r 16000 -b 16 -c 1 -t raw - > "$TEMP_FIFO" 2>/dev/null &
fi
REC_PID=$!

trap 'kill $REC_PID 2>/dev/null' SIGINT

while kill -0 $REC_PID 2>/dev/null; do
    if read -r -t 0.1; then
        kill $REC_PID 2>/dev/null
        break
    fi
done

wait $REC_PID 2>/dev/null
trap - SIGINT

echo "-> Audio capture stopped. Waiting for server inference..."

wait $CURL_PID

RESPONSE=$(cat "$TEMP_RESP" 2>/dev/null)
rm -f "$TEMP_FIFO" "$TEMP_RESP"

TEXT=$(echo "$RESPONSE" | jq -r '.text' 2>/dev/null)

if [ -z "$TEXT" ] || [ "$TEXT" == "null" ]; then
     echo "❌ Error parsing server response:"
     echo "$RESPONSE"
     exit 1
fi

echo -e "\n$TEXT\n"

if command -v wl-copy &> /dev/null; then
    echo -n "$TEXT" | wl-copy
    echo "✅ Copied to Wayland clipboard."
elif command -v xclip &> /dev/null; then
    echo -n "$TEXT" | xclip -selection clipboard
    echo "✅ Copied to X11 clipboard."
elif command -v pbcopy &> /dev/null; then
    echo -n "$TEXT" | pbcopy
    echo "✅ Copied to macOS clipboard."
else
    echo "⚠️ No clipboard utility found on client. Text printed above."
fi