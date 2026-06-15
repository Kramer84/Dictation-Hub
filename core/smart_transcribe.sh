#!/bin/bash

source "$BASH_PROFILE_DIR/modules/speech2text/whisper_core.sh"

function smart_transcribe() {
    # 1. Setup Directories
    local base_dir="$HOME/.whisper_transcriptions"
    local date_str=$(date +"%Y%m%d")
    local time_str=$(date +"%H%M%S")
    local temp_id="${date_str}_${time_str}_processing"
    local session_dir="$base_dir/$temp_id"

    mkdir -p "$session_dir"

    # 2. Recording
    local wav_file_raw="$session_dir/recording_raw.wav"
    local wav_file_16bit="$session_dir/recording_16bit.wav"

    echo "=== Smart Transcription ==="
    echo "-> Recording... (Press Enter to stop)"

    arecord -f cd -t wav "$wav_file_raw" &>/dev/null &
    local rec_pid=$!

    read -r
    kill $rec_pid 2>/dev/null
    wait $rec_pid 2>/dev/null

    # 3. Audio Processing
    echo "-> Processing Audio..."
    ffmpeg -hide_banner -loglevel error -y -i "$wav_file_raw" -ar 16000 -ac 1 -c:a pcm_s16le "$wav_file_16bit"

    # Get Duration
    local duration=$(ffmpeg -i "$wav_file_16bit" 2>&1 | grep "Duration" | cut -d ' ' -f 4 | sed s/,//)
    echo "   Audio Duration: $duration"

    # Cleanup RAW (keep only 16bit as requested)
    rm -f "$wav_file_raw"

    # 4. Sequential Transcription
    local configs_dir="$BASH_PROFILE_DIR/configs/whisper"
    local output_jsons=() # CHANGED: We now track JSONs

    echo "-> Running Whisper Profiles (Sequential)..."

    for config in "$configs_dir"/*.env; do
        [ -e "$config" ] || continue

        local config_name=$(basename "$config" .env)
        local out_base="$session_dir/${config_name}"

        printf "   [..] Profile: %-10s " "$config_name"

        # Timing the execution
        local start_ts=$(date +%s)
        run_whisper_core "$wav_file_16bit" "$config" "$out_base"
        local end_ts=$(date +%s)

        echo "Done ($((end_ts - start_ts))s)"
        output_jsons+=("${out_base}.json") # Capture JSON path
    done

    # 5. AI Post-Processing
    local final_output="$session_dir/FINAL.md"
    local py_script="$BASH_PROFILE_DIR/modules/speech2text/process_transcriptions.py"
    local python_cmd="${PYTHON_CORE:-python3}"

    echo "-> AI Post-Processing (JSON Source + Repetition Markers)..."
    "$python_cmd" "$py_script" \
        --files "${output_jsons[@]}" \
        --target "$final_output" \
        --agent "${MISTRAL_AGENT_ID_TRANSCRIPTION}"

    # 6. Rename & Cleanup
    local meta_file="$session_dir/final_metadata.json"
    if [ -f "$meta_file" ]; then
        local theme=$(grep -oP '"theme": "\K[^"]+' "$meta_file" | tr ' ' '_')
        local desc=$(grep -oP '"descriptor": "\K[^"]+' "$meta_file" | tr ' ' '_')
        local new_id="${date_str}_${time_str}_${theme}_${desc}"
        mv "$session_dir" "$base_dir/$new_id"
        final_output="$base_dir/$new_id/FINAL.md"
        echo -e "\n-> Session Renamed: $new_id"
    fi

    # 7. Display
    echo -e "\n"
    if command -v batcat &> /dev/null; then
        batcat -P -p -l md "$final_output"
    else
        cat "$final_output"
    fi
}
