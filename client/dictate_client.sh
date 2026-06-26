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

# 1. Terminate ghost processes locking the microphone
killall arecord 2>/dev/null
killall rec 2>/dev/null

TEMP_FIFO="/tmp/dictation_fifo_$$"
TEMP_RESP="/tmp/dictation_resp_$$"
TEMP_ERR="/tmp/dictation_err_$$"
TEMP_STOP="/tmp/dictation_stop_$$"

rm -f "$TEMP_STOP" "$TEMP_ERR"
mkfifo "$TEMP_FIFO"

echo "🎙️ Recording and streaming... Press [Enter] or [Ctrl+C] to stop."

# 2. Launch curl stream listener in background
curl -s -X POST -T - "http://$SERVER_IP:$SERVER_PORT/transcribe" < "$TEMP_FIFO" > "$TEMP_RESP" &
CURL_PID=$!

# 3. Launch audio capture, intercepting hardware errors to a file instead of /dev/null
if [ "$AUDIO_BACKEND" == "arecord" ]; then
    arecord -f cd -t wav > "$TEMP_FIFO" 2> "$TEMP_ERR" &
else
    rec -r 44100 -b 16 -c 1 -t wav - > "$TEMP_FIFO" 2> "$TEMP_ERR" &
fi
REC_PID=$!

# 4. Asynchronous kill-switches (Bypassing fragile bash timeout loops)
sleep 0.5 # Buffer to prevent accidental double-taps of the Enter key on launch
( read -r; touch "$TEMP_STOP" ) &
READ_PID=$!

trap 'touch "$TEMP_STOP"' SIGINT

# 5. Core execution lock
while kill -0 $REC_PID 2>/dev/null; do
    if [ -f "$TEMP_STOP" ]; then
        kill $REC_PID 2>/dev/null
        break
    fi
    sleep 0.1
done

# 6. Hardware Failure Diagnosis Check
if [ ! -f "$TEMP_STOP" ]; then
    echo "❌ Audio capture died instantly. Hardware Error Log:"
    cat "$TEMP_ERR"
    # Ensure curl closes out gracefully if the pipeline failed early
    kill $CURL_PID 2>/dev/null
    exit 1
fi

# Cleanup listeners
kill $READ_PID 2>/dev/null
trap - SIGINT

echo "-> Audio capture stopped. Waiting for server inference..."

wait $CURL_PID

RESPONSE=$(cat "$TEMP_RESP" 2>/dev/null)
rm -f "$TEMP_FIFO" "$TEMP_RESP" "$TEMP_ERR" "$TEMP_STOP"

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