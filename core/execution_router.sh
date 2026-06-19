#!/bin/bash
# core/execution_router.sh

if ! command -v jq &> /dev/null; then
    echo "Error: jq is required. Install via: sudo apt install jq"
    exit 1
fi

CONFIG_STATIC="configs/static.json"
CONFIG_FULL="configs/standard.env"

if [[ ! -f "$CONFIG_STATIC" ]]; then
    echo "Error: Configuration file $CONFIG_STATIC not found."
    exit 1
fi

if [[ ! -f "$CONFIG_FULL" ]]; then
    echo "Error: Configuration file $CONFIG_FULL not found."
    exit 1
fi

# Dynamically build the workspace layout
RAW_BASE_DIR=$(jq -r '.storage.base_dir' "$CONFIG_STATIC")
BASE_DIR="${RAW_BASE_DIR/#\~/$HOME}"
FORMAT=$(jq -r '.storage.folder_format' "$CONFIG_STATIC")
TIMESTAMP=$(date +"$FORMAT")

WORKSPACE="$BASE_DIR/$TIMESTAMP"
mkdir -p "$WORKSPACE"

FILE_WAV="$WORKSPACE/${TIMESTAMP}$(jq -r '.suffixes.audio' "$CONFIG_STATIC")"
FILE_JSON="$WORKSPACE/${TIMESTAMP}$(jq -r '.suffixes.full_json' "$CONFIG_STATIC")"

echo "========================================================"
echo " Workspace Created: $WORKSPACE"
echo "========================================================"

# 1. Blocking Audio Capture
bash core/audio_capture.sh --output "$FILE_WAV" --normalize 

# 2. Sequential Transcription
if [[ -f "$FILE_WAV" ]]; then
    echo "[Router] Audio capture finalized. Booting Whisper inference..."
    
    bash core/whisper_transcribe.sh \
        --input "$FILE_WAV" \
        --config "$CONFIG_FULL" \
        --output "$FILE_JSON"

    echo "[Router] JSON Ground Truth generated at $FILE_JSON"
    
    # 3. Configuration-Aware Post-Processing
    if [[ -f "$FILE_JSON" ]]; then
        echo "[Router] Booting deterministic cleaner..."
        
        # Parse post-processing flags from standard.env (Requires you to add them to standard.env)
        # Fallback to "false" if the user has not defined them yet.
        MARK_CONFIDENCE=$(grep "^MARK_CONFIDENCE=" "$CONFIG_FULL" | cut -d'"' -f2 || echo "false")
        COMPRESS_REPETITIONS=$(grep "^COMPRESS_REPETITIONS=" "$CONFIG_FULL" | cut -d'"' -f2 || echo "false")

        # Dynamically build Python arguments
        PY_ARGS=()
        if [[ "$MARK_CONFIDENCE" == "true" ]]; then
            PY_ARGS+=("--mark-confidence")
        fi
        if [[ "$COMPRESS_REPETITIONS" == "true" ]]; then
            PY_ARGS+=("--compress-repetitions")
        fi

        # Execute Python script with dynamic arguments
        python3 post_processing/deterministic_cleaner.py "${PY_ARGS[@]}" "$FILE_JSON"
        
        if [[ $? -eq 0 ]]; then
            echo "[Router] Post-processing complete."
        else
            echo "[Router] Error: deterministic_cleaner.py failed."
            exit 1
        fi
    else
        echo "[Router] Error: $FILE_JSON was not created. Skipping post-processing."
        exit 1
    fi

    echo "[Router] Pipeline sequence complete."
else
    echo "[Router] Error: $FILE_WAV was not created. Aborting transcription."
    exit 1
fi