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
        --*) KEY="${1#--}"; OVERRIDES[$KEY]="$2"; shift 2 ;;
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

if [[ ! -f "$CONFIG_FULL" ]]; then
    echo "Error: Base environment $CONFIG_FULL not found."
    exit 1
fi

RAW_BASE_DIR=$(jq -r '.storage.base_dir' "$CONFIG_STATIC")
BASE_DIR="${RAW_BASE_DIR/#\~/$HOME}"
FORMAT=$(jq -r '.storage.folder_format' "$CONFIG_STATIC")
TIMESTAMP=$(date +"$FORMAT")

# Apply folder suffix
WORKSPACE="$BASE_DIR/${TIMESTAMP}_${PROFILE}"

mkdir -p "$WORKSPACE"
FILE_WAV="$WORKSPACE/${TIMESTAMP}$(jq -r '.suffixes.audio' "$CONFIG_STATIC")"
FILE_JSON="$WORKSPACE/${TIMESTAMP}$(jq -r '.suffixes.full_json' "$CONFIG_STATIC")"

echo "========================================================"
echo " Workspace Created: $WORKSPACE (Profile: $PROFILE)"
echo "========================================================"

TEMP_ENV=$(mktemp)
cat "$CONFIG_FULL" > "$TEMP_ENV"
echo -e "\n# --- DYNAMIC OVERRIDES ---" >> "$TEMP_ENV"

# Inject JSON hard overrides first
jq -r ".profiles[\"$PROFILE\"].env_overrides | to_entries[] | \"\(.key)=\\\"\(.value)\\\"\"" "$CONFIG_JSON" 2>/dev/null >> "$TEMP_ENV"

# Inject CLI arguments second
for KEY in "${!OVERRIDES[@]}"; do
    if echo "$VALID_ARGS" | grep -qw "$KEY"; then
        UPPER_KEY=$(echo "$KEY" | tr 'a-z' 'A-Z')
        echo "${UPPER_KEY}=\"${OVERRIDES[$KEY]}\"" >> "$TEMP_ENV"
    else
        echo "[Router] Warning: Argument '$KEY' is not in valid_arguments."
    fi
done

bash "$SCRIPT_DIR/audio_capture.sh" --output "$FILE_WAV" --normalize 

if [[ -f "$FILE_WAV" ]]; then
    echo "[Router] Audio capture finalized. Booting Whisper inference..."
    
    bash "$SCRIPT_DIR/whisper_transcribe.sh" \
        --input "$FILE_WAV" \
        --config "$TEMP_ENV" \
        --output "$FILE_JSON"

    if [[ -f "$FILE_JSON" ]]; then
        # Extract Language & Metadata
        LANG_CODE=$(jq -r '.result.language // "auto"' "$FILE_JSON" 2>/dev/null)
        cat <<EOF > "$WORKSPACE/metadata.json"
{
  "profile": "$PROFILE",
  "language": "$LANG_CODE",
  "timestamp": "$TIMESTAMP"
}
EOF

        echo "[Router] Booting deterministic cleaner..."
        
        # Check if config forced confidence markers
        MARK_CONF=$(grep "^MARK_CONFIDENCE=" "$TEMP_ENV" | cut -d'"' -f2 || echo "false")
        if [[ -z "$MARK_CONF" ]]; then
            MARK_CONF=$(grep "^MARK_CONFIDENCE=" "$CONFIG_FULL" | cut -d'"' -f2 || echo "false")
        fi

        # Check if config forced repetition compression marker
        MARK_COMP=$(grep "^COMPRESS_REPETITIONS=" "$TEMP_ENV" | cut -d'"' -f2 || echo "false")
        if [[ -z "$MARK_COMP" ]]; then
            MARK_COMP=$(grep "^COMPRESS_REPETITIONS=" "$CONFIG_FULL" | cut -d'"' -f2 || echo "false")
        fi

        rm "$TEMP_ENV"

        PY_ARGS=()
        [[ "$MARK_CONF" == "true" ]] && PY_ARGS+=("--mark-confidence")
        [[ "$MARK_COMP" == "true" ]] && PY_ARGS+=("--compress-repetitions")

        python3 "$REPO_ROOT/post_processing/deterministic_cleaner.py" "${PY_ARGS[@]}" "$FILE_JSON"
        
        RAW_TEXT=$(jq -r '.segments[].text' "${FILE_JSON%.*}_cleaned.json" | tr -d '\n')
        RAW_TXT_PATH="${FILE_JSON%.*}_raw.txt"
        echo -n "$RAW_TEXT" > "$RAW_TXT_PATH"

        FINAL_TEXT="$RAW_TEXT"
        CURRENT_INPUT="$RAW_TXT_PATH"

        # --- Dynamic Post-Processing Execution ---
        STEP=1
        while IFS= read -r step_json; do
            if [[ -z "$step_json" || "$step_json" == "null" ]]; then
                continue
            fi
            
            STEP_TYPE=$(echo "$step_json" | jq -r '.type // empty')
            STEP_OUT="${FILE_JSON%.*}_step${STEP}.txt"
            
            if [[ "$STEP_TYPE" == "llm" ]]; then
                PROVIDER=$(echo "$step_json" | jq -r '.provider // "local"')
                MODEL=$(echo "$step_json" | jq -r '.model // "llama3"')
                ENDPOINT=$(echo "$step_json" | jq -r '.endpoint // "http://localhost:11434/v1/chat/completions"')
                PROMPT=$(echo "$step_json" | jq -r '.prompt // empty')
                ENFORCE_JSON=$(echo "$step_json" | jq -r '.enforce_json // false')
                
                CMD="python3 \"$REPO_ROOT/post_processing/llm_step_runner.py\""
                CMD="$CMD --input \"$CURRENT_INPUT\" --output \"$STEP_OUT\""
                CMD="$CMD --provider \"$PROVIDER\" --model \"$MODEL\" --endpoint \"$ENDPOINT\""
                CMD="$CMD --language \"$LANG_CODE\" --prompt \"$PROMPT\""
                
                if [[ "$ENFORCE_JSON" == "true" ]]; then
                    CMD="$CMD --enforce-json"
                fi
                
                echo "[Router] Running Post-Processing Step $STEP ($PROVIDER / $MODEL)..."
                eval "$CMD"
                
            elif [[ "$STEP_TYPE" == "deterministic" ]]; then
                SCRIPT_NAME=$(echo "$step_json" | jq -r '.script // empty')
                DICT_PATH=$(echo "$step_json" | jq -r '.dictionary // empty')
                LANG_ARG=$(echo "$step_json" | jq -r '.language // empty')
                EXTRA_ARGS=$(echo "$step_json" | jq -r '.args // empty')
                
                CMD="python3 \"$REPO_ROOT/post_processing/$SCRIPT_NAME\" --input \"$CURRENT_INPUT\" --output \"$STEP_OUT\""
                
                if [[ -n "$DICT_PATH" && "$DICT_PATH" != "null" ]]; then
                    CMD="$CMD --dict \"$REPO_ROOT/$DICT_PATH\""
                fi
                if [[ "$LANG_ARG" == "{language}" ]]; then
                    CMD="$CMD --language \"$LANG_CODE\""
                fi
                if [[ -n "$EXTRA_ARGS" && "$EXTRA_ARGS" != "null" ]]; then
                    CMD="$CMD $EXTRA_ARGS"
                fi
                
                echo "[Router] Running Deterministic Step $STEP ($SCRIPT_NAME)..."
                eval "$CMD"
            else
                continue
            fi
            
            if [[ $? -ne 0 ]]; then
                echo "[Router] Error during post-processing step $STEP."
                exit 1
            fi
            
            CURRENT_INPUT="$STEP_OUT"
            FINAL_TEXT=$(cat "$CURRENT_INPUT")
            ((STEP++))
            
        done < <(jq -c ".profiles[\"$PROFILE\"].post_processing[]?" "$CONFIG_JSON" 2>/dev/null)

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
    echo "[Router] Error: $FILE_WAV was not created. Aborting transcription."
    exit 1
fi