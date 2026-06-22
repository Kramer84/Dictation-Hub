#!/bin/bash
# core/execution_router.sh

if ! command -v jq &> /dev/null; then
    echo "Error: jq is required. Install via: sudo apt install jq"
    exit 1
fi

# ---------------------------------------------------------
# Dynamic Path Resolution (Symlink Proof)
# ---------------------------------------------------------
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
# ---------------------------------------------------------

CONFIG_STATIC="$REPO_ROOT/configs/static.json"
CONFIG_FULL="$REPO_ROOT/configs/standard.env"

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
bash "$SCRIPT_DIR/audio_capture.sh" --output "$FILE_WAV" --normalize 

# 2. Sequential Transcription
if [[ -f "$FILE_WAV" ]]; then
    echo "[Router] Audio capture finalized. Booting Whisper inference..."
    
    bash "$SCRIPT_DIR/whisper_transcribe.sh" \
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

        python3 "$REPO_ROOT/post_processing/deterministic_cleaner.py" "${PY_ARGS[@]}" "$FILE_JSON"
        
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

    # Clipboard Injection
    if command -v wl-copy &> /dev/null; then
        jq -r '.segments[].text' "${FILE_JSON%.*}_cleaned.json" | tr -d '\n' | wl-copy
        echo "[Router] Cleaned text copied to Wayland clipboard."
    elif command -v xclip &> /dev/null; then
        jq -r '.segments[].text' "${FILE_JSON%.*}_cleaned.json" | tr -d '\n' | xclip -selection clipboard
        echo "[Router] Cleaned text copied to X11 clipboard."
    else
        echo "[Router] Warning: Neither xclip nor wl-copy found. Cannot copy to clipboard."
    fi
    echo "[Router] Pipeline sequence complete."
else
    echo "[Router] Error: $FILE_WAV was not created. Aborting transcription."
    exit 1
fi