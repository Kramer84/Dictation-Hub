#!/bin/bash
# client/dictate_client.sh

# Resolve physical path to find the client.env file dynamically
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
    echo "Please copy client.env.template to client.env and configure your Tailscale IP."
    exit 1
fi

source "$ENV_FILE"

# Ensure jq is installed
if ! command -v jq &> /dev/null; then
    echo "Error: jq is required to parse the server response."
    exit 1
fi

TEMP_WAV="/tmp/dictation_capture_$$.wav"

echo "🎙️ Recording audio... Press [Enter] to stop."

if [ "$AUDIO_BACKEND" == "arecord" ]; then
    arecord -f cd -t wav "$TEMP_WAV" &>/dev/null &
else
    # Fallback for macOS utilizing sox
    rec -r 44100 -b 16 -c 1 "$TEMP_WAV" &>/dev/null &
fi

REC_PID=$!
read -r
kill $REC_PID 2>/dev/null
wait $REC_PID 2>/dev/null

echo "-> Transmitting to Tailscale node ($SERVER_IP:$SERVER_PORT)..."

# Fire the audio to the FastAPI endpoint
RESPONSE=$(curl -s -X POST "http://$SERVER_IP:$SERVER_PORT/transcribe" \
     -H "Content-Type: multipart/form-data" \
     -F "file=@$TEMP_WAV")

rm -f "$TEMP_WAV"

if [ $? -ne 0 ]; then
    echo "❌ Error: Failed to connect to server via Tailscale."
    exit 1
fi

# Extract text payload
TEXT=$(echo "$RESPONSE" | jq -r '.text' 2>/dev/null)

if [ -z "$TEXT" ] || [ "$TEXT" == "null" ]; then
     echo "❌ Error parsing server response:"
     echo "$RESPONSE"
     exit 1
fi

echo -e "\n$TEXT\n"

# Client-Side Clipboard Injection
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