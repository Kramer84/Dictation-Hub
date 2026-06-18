#!/bin/bash
# core/execution_router.sh

if ! command -v jq &> /dev/null; then
    echo "Error: jq is required. Install via: sudo apt install jq"
    exit 1
fi

CONFIG_STATIC="configs/static.json"
CONFIG_LIVE="configs/live_transcription_preview.env"
CONFIG_FULL="configs/standard.env"

if [[ ! -f "$CONFIG_STATIC" ]]; then
    echo "Error: Configuration file $CONFIG_STATIC not found."
    exit 1
fi

RAW_BASE_DIR=$(jq -r '.storage.base_dir' "$CONFIG_STATIC")
BASE_DIR="${RAW_BASE_DIR/#\~/$HOME}"
FORMAT=$(jq -r '.storage.folder_format' "$CONFIG_STATIC")
TIMESTAMP=$(date +"$FORMAT")

WORKSPACE="$BASE_DIR/$TIMESTAMP"
mkdir -p "$WORKSPACE"

FILE_WAV="$WORKSPACE/${TIMESTAMP}$(jq -r '.suffixes.audio' "$CONFIG_STATIC")"
FILE_LIVE="$WORKSPACE/${TIMESTAMP}$(jq -r '.suffixes.live_text' "$CONFIG_STATIC")"
FILE_JSON="$WORKSPACE/${TIMESTAMP}$(jq -r '.suffixes.full_json' "$CONFIG_STATIC")"

echo "========================================================"
echo " Workspace Created: $WORKSPACE"
echo "========================================================"

PID_AUDIO=""
PID_LIVE=""

transition_to_post_processing() {
    echo -e "\n\n[Router] Dictation ended. Intercepting signal and halting capture..."
    
    # 1. Terminate child binaries (The Zombie Fix)
    pkill -P $PID_LIVE 2>/dev/null
    pkill -P $PID_AUDIO 2>/dev/null
    
    # 2. Terminate the bash wrappers
    kill $PID_LIVE 2>/dev/null
    kill $PID_AUDIO 2>/dev/null
    
    # 3. Aggressive safety net to release hardware locks
    killall -q whisper-stream arecord ffmpeg 2>/dev/null
    wait $PID_AUDIO 2>/dev/null
    
    echo "[Router] Finalized raw audio segment: $FILE_WAV"
    echo "[Router] Booting primary whisper_transcribe.sh on the full audio file..."

    # Pass the standard.env, NOT the static.json
    bash core/whisper_transcribe.sh \
        --input "$FILE_WAV" \
        --config "$CONFIG_FULL" \
        --output "$FILE_JSON"

    echo "[Router] JSON Ground Truth generated at $FILE_JSON"
    echo "[Router] Pipeline sequence complete."
    exit 0
}

trap transition_to_post_processing SIGINT

echo "[Router] Initializing parallel capture and live preview..."
echo "[Router] Press [Ctrl+C] when dictation is complete."

bash core/audio_capture.sh --output "$FILE_WAV" &
PID_AUDIO=$!

bash core/live_dictate.sh --config "$CONFIG_LIVE" --output "$FILE_LIVE" &
PID_LIVE=$!

wait $PID_LIVE