#!/bin/bash
# core/execution_router.sh

if ! command -v jq &> /dev/null; then
    echo "Error: jq is required. Install via: sudo apt install jq"
    exit 1
fi

SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

CONFIG_STATIC="$REPO_ROOT/configs/static.json"
CONFIG_JSON="$REPO_ROOT/configs/pipeline_config.json"

if [[ ! -f "$CONFIG_STATIC" || ! -f "$CONFIG_JSON" ]]; then
    echo "Error: Required configurations missing."
    exit 1
fi

PROFILE="standard"
if [[ $# -gt 0 && ! "$1" == --* ]]; then
    PROFILE="$1"
    shift
fi

declare -A OVERRIDES
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --*)
            KEY="${1#--}"
            OVERRIDES[$KEY]="$2"
            shift 2
            ;;
        *) shift ;;
    esac
done

PROFILE_EXISTS=$(jq -e ".profiles[\"$PROFILE\"]" "$CONFIG_JSON" >/dev/null; echo $?)
if [ "$PROFILE_EXISTS" -ne 0 ]; then
    echo "[Router] Profile '$PROFILE' not found. Defaulting to standard."
    PROFILE="standard"
fi

BASE_ENV=$(jq -r ".profiles[\"$PROFILE\"].env" "$CONFIG_JSON")
VALID_ARGS=$(jq -r ".valid_arguments[]?" "$CONFIG_JSON")
CONFIG_FULL="$REPO_ROOT/configs/$BASE_ENV"

RAW_BASE_DIR=$(jq -r '.storage.base_dir' "$CONFIG_STATIC")
BASE_DIR="${RAW_BASE_DIR/#\~/$HOME}"
FORMAT=$(jq -r '.storage.folder_format' "$CONFIG_STATIC")
TIMESTAMP=$(date +"$FORMAT")

# --- Dynamic Naming ---
WORKSPACE_NAME="${TIMESTAMP}_${PROFILE}"
WORKSPACE="$BASE_DIR/$WORKSPACE_NAME"
mkdir -p "$WORKSPACE"

FILE_WAV="$WORKSPACE/${WORKSPACE_NAME}$(jq -r '.suffixes.audio' "$CONFIG_STATIC")"
FILE_JSON="$WORKSPACE/${WORKSPACE_NAME}$(jq -r '.suffixes.full_json' "$CONFIG_STATIC")"

echo "========================================================"
echo " Workspace Created: $WORKSPACE"
echo "========================================================"

TEMP_ENV=$(mktemp)
cat "$CONFIG_FULL" > "$TEMP_ENV"
echo -e "\n# --- DYNAMIC OVERRIDES ---" >> "$TEMP_ENV"
for KEY in "${!OVERRIDES[@]}"; do
    if echo "$VALID_ARGS" | grep -qw "$KEY"; then
        UPPER_KEY=$(echo "$KEY" | tr 'a-z' 'A-Z')
        echo "${UPPER_KEY}=\"${OVERRIDES[$KEY]}\"" >> "$TEMP_ENV"
    fi
done

bash "$SCRIPT_DIR/audio_capture.sh" --output "$FILE_WAV" --normalize 

if [[ -f "$FILE_WAV" ]]; then
    echo "[Router] Audio capture finalized. Booting Whisper inference..."
    
    bash "$SCRIPT_DIR/whisper_transcribe.sh" \
        --input "$FILE_WAV" \
        --config "$TEMP_ENV" \
        --output "$FILE_JSON"

    rm "$TEMP_ENV"

    if [[ -f "$FILE_JSON" ]]; then
        # --- Metadata & Language Extraction ---
        LANG_DETECTED=$(jq -r '.language // "unknown"' "$FILE_JSON")
        cat <<EOF > "$WORKSPACE/metadata.json"
{
  "timestamp": "$TIMESTAMP",
  "profile": "$PROFILE",
  "detected_language": "$LANG_DETECTED"
}
EOF
        
        echo "[Router] Booting deterministic cleaner..."
        POST_STEPS=$(jq -r ".profiles[\"$PROFILE\"].post_processing[]?" "$CONFIG_JSON" 2>/dev/null)
        
        PY_ARGS=("--compress-repetitions")
        # Force confidence markers if we are passing this to an LLM
        if [[ -n "$POST_STEPS" ]]; then
            PY_ARGS+=("--mark-confidence")
        fi

        python3 "$REPO_ROOT/post_processing/deterministic_cleaner.py" "${PY_ARGS[@]}" "$FILE_JSON"
        
        RAW_TEXT=$(jq -r '.segments[].text' "${FILE_JSON%.*}_cleaned.json" | tr -d '\n')
        RAW_TXT_PATH="${FILE_JSON%.*}_raw.txt"
        echo -n "$RAW_TEXT" > "$RAW_TXT_PATH"

        FINAL_TEXT="$RAW_TEXT"
        CURRENT_INPUT="$RAW_TXT_PATH"

        if [[ -n "$POST_STEPS" ]]; then
            STEP=1
            while IFS= read -r script_cmd; do
                if [ -n "$script_cmd" ]; then
                    STEP_OUT="${FILE_JSON%.*}_step${STEP}.txt"
                    cmd="${script_cmd//\{repo_root\}/$REPO_ROOT}"
                    # Dynamically inject the language argument
                    cmd="$cmd --input \"$CURRENT_INPUT\" --output \"$STEP_OUT\" --language \"$LANG_DETECTED\""
                    
                    echo "[Router] Running Post-Processing Step $STEP..."
                    eval "$cmd"
                    
                    if [[ $? -ne 0 ]]; then
                        echo "[Router] Error during post-processing step $STEP."
                        exit 1
                    fi
                    CURRENT_INPUT="$STEP_OUT"
                    FINAL_TEXT=$(cat "$CURRENT_INPUT")
                    ((STEP++))
                fi
            done <<< "$POST_STEPS"
        fi

        touch "$WORKSPACE/.completed"

        echo -e "\n=== RAW TEXT ==="
        echo "$RAW_TEXT"
        
        if [[ "$RAW_TEXT" != "$FINAL_TEXT" ]]; then
            echo -e "\n=== POST-PROCESSED TEXT ==="
            echo "$FINAL_TEXT"
        fi

        TEXT_TO_COPY="${FINAL_TEXT:-$RAW_TEXT}"

        if command -v wl-copy &> /dev/null; then
            echo -n "$TEXT_TO_COPY" | wl-copy
            echo -e "\n[Router] Copied to Wayland clipboard."
        elif command -v xclip &> /dev/null; then
            echo -n "$TEXT_TO_COPY" | xclip -selection clipboard
            echo -e "\n[Router] Copied to X11 clipboard."
        fi
    else
        echo "[Router] Error: $FILE_JSON was not created."
        exit 1
    fi
else
    echo "[Router] Error: $FILE_WAV was not created."
    exit 1
fi