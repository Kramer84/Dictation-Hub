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

killall arecord parec parecord rec ffmpeg 2>/dev/null

TEMP_FIFO="/tmp/dictation_fifo_$$"
TEMP_RESP="/tmp/dictation_resp_$$"
TEMP_ERR="/tmp/dictation_err_$$"

rm -f "$TEMP_ERR" "$TEMP_FIFO" "$TEMP_RESP"
mkfifo "$TEMP_FIFO"

# 1. Open the dummy FD to keep the pipe alive
exec 3<> "$TEMP_FIFO"

# 2. Launch curl. 3>&- prevents curl from inheriting the write FD and causing a deadlock.
curl -s -X POST -H "Transfer-Encoding: chunked" -H "Expect:" --data-binary @- "http://$SERVER_IP:$SERVER_PORT/transcribe" < "$TEMP_FIFO" > "$TEMP_RESP" 3>&- &
CURL_PID=$!

SELECTED_BACKEND=""

launch_capture() {
    if command -v parecord &> /dev/null; then
        > "$TEMP_ERR"
        # 3>&- isolates the background process from the dummy pipe lock
        parecord --file-format=wav > "$TEMP_FIFO" 2> "$TEMP_ERR" 3>&- &
        REC_PID=$!
        sleep 0.2
        if kill -0 $REC_PID 2>/dev/null; then
            SELECTED_BACKEND="parecord (PulseAudio/PipeWire)"
            return 0
        fi
    fi

    if command -v rec &> /dev/null; then
        > "$TEMP_ERR"
        rec -q -r 44100 -b 16 -c 1 -t wav - > "$TEMP_FIFO" 2> "$TEMP_ERR" 3>&- &
        REC_PID=$!
        sleep 0.2
        if kill -0 $REC_PID 2>/dev/null; then
            SELECTED_BACKEND="rec (SoX)"
            return 0
        fi
    fi

    if command -v arecord &> /dev/null; then
        > "$TEMP_ERR"
        arecord -D pulse -f cd -t wav > "$TEMP_FIFO" 2> "$TEMP_ERR" 3>&- &
        REC_PID=$!
        sleep 0.2
        if kill -0 $REC_PID 2>/dev/null; then
            SELECTED_BACKEND="arecord (pulse bridge)"
            return 0
        fi

        > "$TEMP_ERR"
        arecord -D plughw:1,0 -f cd -t wav > "$TEMP_FIFO" 2> "$TEMP_ERR" 3>&- &
        REC_PID=$!
        sleep 0.2
        if kill -0 $REC_PID 2>/dev/null; then
            SELECTED_BACKEND="arecord (plughw:1,0)"
            return 0
        fi

        > "$TEMP_ERR"
        arecord -D default -f cd -t wav > "$TEMP_FIFO" 2> "$TEMP_ERR" 3>&- &
        REC_PID=$!
        sleep 0.2
        if kill -0 $REC_PID 2>/dev/null; then
            SELECTED_BACKEND="arecord (default)"
            return 0
        fi
    fi

    return 1
}

launch_capture
if [ $? -ne 0 ]; then
    echo "❌ Audio capture died instantly. Hardware Error Log:"
    cat "$TEMP_ERR"
    exec 3>&-
    kill $CURL_PID 2>/dev/null
    rm -f "$TEMP_FIFO" "$TEMP_RESP" "$TEMP_ERR"
    exit 1
fi

echo "✅ Audio Backend Secured: $SELECTED_BACKEND"
echo "🎙️ Recording and streaming... Press [Enter] or [Ctrl+C] to stop."

while read -r -t 0.1; do :; done

# Use SIGTERM (kill). Background jobs in bash ignore SIGINT.
trap 'kill $REC_PID 2>/dev/null' SIGINT

while kill -0 $REC_PID 2>/dev/null; do
    if read -r -t 0.1; then
        kill $REC_PID 2>/dev/null
        break
    fi
done

trap - SIGINT

# Send EOF to curl by dropping the main script's lock on the pipe
exec 3>&-

echo "-> Audio capture stopped. Waiting for server inference..."
wait $CURL_PID

RESPONSE=$(cat "$TEMP_RESP" 2>/dev/null)

TEXT=$(echo "$RESPONSE" | jq -r '.text' 2>/dev/null)

if [[ "$TEXT" == *"empty audio stream"* ]]; then
     echo "❌ Server rejected stream. Client-side Hardware Error Log:"
     cat "$TEMP_ERR" 2>/dev/null
     rm -f "$TEMP_FIFO" "$TEMP_RESP" "$TEMP_ERR"
     exit 1
fi

if [ -z "$TEXT" ] || [ "$TEXT" == "null" ]; then
     echo "❌ Error parsing server response:"
     echo "$RESPONSE"
     rm -f "$TEMP_FIFO" "$TEMP_RESP" "$TEMP_ERR"
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

rm -f "$TEMP_FIFO" "$TEMP_RESP" "$TEMP_ERR"