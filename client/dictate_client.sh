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

# --- Dynamic Argument Parsing ---
PROFILE="standard"
if [[ $# -gt 0 && ! "$1" == --* ]]; then
    PROFILE="$1"
    shift
fi

QUERY_STRING="profile=$PROFILE"
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --*)
            KEY="${1#--}"
            VAL="$2"
            QUERY_STRING="${QUERY_STRING}&${KEY}=${VAL}"
            shift 2
            ;;
        *) shift ;;
    esac
done
# ---------------------------------

killall arecord parec parecord rec ffmpeg 2>/dev/null

TEMP_FIFO="/tmp/dictation_fifo_$$"
TEMP_RESP="/tmp/dictation_resp_$$"
TEMP_ERR="/tmp/dictation_err_$$"

rm -f "$TEMP_ERR" "$TEMP_FIFO" "$TEMP_RESP"
mkfifo "$TEMP_FIFO"

exec 3<> "$TEMP_FIFO"

# Send chunked audio stream with dynamic query parameters appended to the URL
PROTOCOL=${SERVER_PROTOCOL:-http}
curl -s -X POST -H "Transfer-Encoding: chunked" -H "Expect:" --data-binary @- "${PROTOCOL}://$SERVER_IP:$SERVER_PORT/transcribe?${QUERY_STRING}" < "$TEMP_FIFO" > "$TEMP_RESP" 3>&- &
CURL_PID=$!

SELECTED_BACKEND=""
launch_capture() {
    # Initialize and clear the log completely at start
    > "$TEMP_ERR"

    # 1. FFmpeg Profile
    if command -v ffmpeg &> /dev/null; then
        echo "=== Attempting FFmpeg Backend ===" >> "$TEMP_ERR"
        # Added the trailing hyphen (-) right before the redirection operator
        ffmpeg -nostdin -y -f pulse -i default -ac 1 -ar 44100 -f wav - > "$TEMP_FIFO" 2>> "$TEMP_ERR" 3>&- &
        REC_PID=$!
        sleep 0.3; kill -0 $REC_PID 2>/dev/null && { SELECTED_BACKEND="ffmpeg (pulse)"; return 0; }
    fi

    # 2. ALSA Profile
    if command -v arecord &> /dev/null; then
        echo "=== Attempting ALSA (pulse) Backend ===" >> "$TEMP_ERR"
        arecord -D pulse -f cd -t wav > "$TEMP_FIFO" 2>> "$TEMP_ERR" 3>&- &
        REC_PID=$!
        sleep 0.2; kill -0 $REC_PID 2>/dev/null && { SELECTED_BACKEND="arecord (pulse)"; return 0; }

        echo "=== Attempting ALSA (default) Backend ===" >> "$TEMP_ERR"
        arecord -D default -f cd -t wav > "$TEMP_FIFO" 2>> "$TEMP_ERR" 3>&- &
        REC_PID=$!
        sleep 0.2; kill -0 $REC_PID 2>/dev/null && { SELECTED_BACKEND="arecord (default)"; return 0; }
    fi

    # 3. SoX Profile
    if command -v rec &> /dev/null; then
        echo "=== Attempting SoX Rec Backend ===" >> "$TEMP_ERR"
        rec -q -r 44100 -b 16 -c 1 -t wav - > "$TEMP_FIFO" 2>> "$TEMP_ERR" 3>&- &
        REC_PID=$!
        sleep 0.2; kill -0 $REC_PID 2>/dev/null && { SELECTED_BACKEND="rec (SoX)"; return 0; }
    fi

    # 4. Native PulseAudio Profile
    if command -v parecord &> /dev/null; then
        echo "=== Attempting PulseAudio Native Backend ===" >> "$TEMP_ERR"
        parecord --file-format=wav > "$TEMP_FIFO" 2>> "$TEMP_ERR" 3>&- &
        REC_PID=$!
        sleep 0.2; kill -0 $REC_PID 2>/dev/null && { SELECTED_BACKEND="parecord"; return 0; }
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

echo "✅ Audio Backend Secured: $SELECTED_BACKEND (Profile: $PROFILE)"
echo "🎙️ Recording and streaming... Press [Enter] or [Ctrl+C] to stop."

while read -r -t 0.1; do :; done
trap 'kill $REC_PID 2>/dev/null' SIGINT
while kill -0 $REC_PID 2>/dev/null; do
    if read -r -t 0.1; then
        kill $REC_PID 2>/dev/null
        break
    fi
done
trap - SIGINT

exec 3>&-
echo "-> Audio capture stopped. Waiting for server inference..."
wait $CURL_PID

RESPONSE=$(cat "$TEMP_RESP" 2>/dev/null)

if [[ "$RESPONSE" == *"empty audio stream"* ]]; then
     echo "❌ Server rejected stream."
     rm -f "$TEMP_FIFO" "$TEMP_RESP" "$TEMP_ERR"
     exit 1
fi

RAW_TEXT=$(echo "$RESPONSE" | jq -r '.raw_text' 2>/dev/null)
FINAL_TEXT=$(echo "$RESPONSE" | jq -r '.final_text' 2>/dev/null)

if [ -z "$RAW_TEXT" ] || [ "$RAW_TEXT" == "null" ]; then
     echo "❌ Error parsing server response:"
     echo "$RESPONSE"
     rm -f "$TEMP_FIFO" "$TEMP_RESP" "$TEMP_ERR"
     exit 1
fi

echo -e "\n=== RAW TEXT ==="
echo -e "$RAW_TEXT"

if [[ "$RAW_TEXT" != "$FINAL_TEXT" && -n "$FINAL_TEXT" ]]; then
    echo -e "\n=== POST-PROCESSED TEXT ==="
    echo -e "$FINAL_TEXT"
fi

TEXT_TO_COPY="${FINAL_TEXT:-$RAW_TEXT}"

if command -v wl-copy &> /dev/null; then
    echo -n "$TEXT_TO_COPY" | wl-copy
    echo -e "\n✅ Copied to Wayland clipboard."
elif command -v xclip &> /dev/null; then
    echo -n "$TEXT_TO_COPY" | xclip -selection clipboard
    echo -e "\n✅ Copied to X11 clipboard."
elif command -v pbcopy &> /dev/null; then
    echo -n "$TEXT_TO_COPY" | pbcopy
    echo -e "\n✅ Copied to macOS clipboard."
fi

if [[ "$RESPONSE" == *"empty audio stream"* ]]; then
     echo "❌ Server rejected stream."
     if [ -s "$TEMP_ERR" ]; then
         echo -e "\n=== FFmpeg Backend Log ==="
         cat "$TEMP_ERR"
     fi
     rm -f "$TEMP_FIFO" "$TEMP_RESP" "$TEMP_ERR"
     exit 1
fi