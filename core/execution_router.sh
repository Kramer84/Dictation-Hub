#!/bin/bash
# core/execution_router.sh

# Ensure jq is installed to parse static.json
if ! command -v jq &> /dev/null; then
    echo "Error: jq is required. Install via: sudo apt install jq"
    exit 1
fi

CONFIG_STATIC="configs/static.json"
CONFIG_LIVE="configs/live_transcription_preview.env"

if [[ ! -f "$CONFIG_STATIC" ]]; then
    echo "Error: Configuration file $CONFIG_STATIC not found."
    exit 1
fi

# 1. Dynamically build the workspace layout
RAW_BASE_DIR=$(jq -r '.storage.base_dir' "$CONFIG_STATIC")
BASE_DIR="${RAW_BASE_DIR/#\~/$HOME}"
FORMAT=$(jq -r '.storage.folder_format' "$CONFIG_STATIC")
TIMESTAMP=$(date +"$FORMAT")

WORKSPACE="$BASE_DIR/$TIMESTAMP"
mkdir -p "$WORKSPACE"

# 2. Map file suffixes from the static configuration
FILE_WAV="$WORKSPACE/${TIMESTAMP}$(jq -r '.suffixes.audio' "$CONFIG_STATIC")"
FILE_LIVE="$WORKSPACE/${TIMESTAMP}$(jq -r '.suffixes.live_text' "$CONFIG_STATIC")"
FILE_JSON="$WORKSPACE/${TIMESTAMP}$(jq -r '.suffixes.full_json' "$CONFIG_STATIC")"

echo "========================================================"
echo " Workspace Created: $WORKSPACE"
echo "========================================================"

PID_AUDIO=""
PID_LIVE=""

# 3. The Router Transition Protocol
transition_to_post_processing() {
    echo -e "\n\n[Router] Dictation ended. Intercepting signal and halting capture..."
    
    kill $PID_LIVE 2>/dev/null
    kill $PID_AUDIO 2>/dev/null
    wait $PID_AUDIO 2>/dev/null
    
    echo "[Router] Finalized raw audio segment: $FILE_WAV"
    echo "[Router] Booting primary whisper_transcribe.sh on the full audio file..."

    # UPDATED: Using explicit flags
    bash core/whisper_transcribe.sh \
        --input "$FILE_WAV" \
        --config "$CONFIG_STATIC" \
        --output "$FILE_JSON"

    echo "[Router] JSON Ground Truth generated at $FILE_JSON"
    echo "[Router] Pipeline sequence complete."
    exit 0
}

trap transition_to_post_processing SIGINT

echo "[Router] Initializing parallel capture and live preview..."
echo "[Router] Press [Ctrl+C] when dictation is complete."

# 4. Fork execution streams
# UPDATED: Using explicit flags
bash core/audio_capture.sh --output "$FILE_WAV" &
PID_AUDIO=$!

bash core/live_dictate.sh --config "$CONFIG_LIVE" --output "$FILE_LIVE" &
PID_LIVE=$!

wait $PID_LIVE