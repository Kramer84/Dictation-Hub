#!/bin/bash
# core/live_dictate.sh

function live_dictate() {
    local output_base=""
    local config_env=""

    # Enforce syntax loop requiring --config and --output exclusively
    while [[ "$#" -gt 0 ]]; do
        case $1 in
            --output) output_base="$2"; shift 2 ;;
            --config) config_env="$2"; shift 2 ;;
            *) echo "Unknown option: $1"; return 1 ;;
        esac
    done

    if [[ -z "$output_base" ]]; then
        output_base="live_dictation_$(date +"%Y%m%d_%H%M%S")"
        echo "No output file specified via --output. Defaulting to: $output_base"
    fi

    # 1. Source Environment Profile
    if [[ -n "$config_env" && -f "$config_env" ]]; then
        source "$config_env"
    fi

    # 2. Expose Variables with Defaults
    : ${WHISPER_DIR:="$HOME/whisper.cpp"}
    : ${MODEL:="large-v3"}
    : ${THREADS:=8}

    # Streaming Specific Parameters
    : ${STEP_MS:=0}           # 0 enables sliding window VAD mode
    : ${LENGTH_MS:=10000}     # Transcribe the last N milliseconds upon silence
    : ${VAD_THOLD:=0.60}      # Higher value = more aggressive silence detection
    : ${LANGUAGE:="auto"}
    : ${SAVE_AUDIO:="false"}  # Save the raw microphone capture to a .wav file

    local cli_exec="${WHISPER_DIR}/build/bin/whisper-stream"
    local model_path="${WHISPER_DIR}/models/ggml-${MODEL}.bin"

    if [[ ! -x "$cli_exec" ]]; then
        echo "Error: whisper-stream not found at $cli_exec."
        echo "Did you compile with 'make WHISPER_SDL2=1'?"
        return 1
    fi

    # 3. Command Construction
    local cmd="\"$cli_exec\" -m \"$model_path\" -t $THREADS"
    cmd+=" --step $STEP_MS --length $LENGTH_MS -vth $VAD_THOLD"
    cmd+=" -l \"$LANGUAGE\""
    cmd+=" -f \"${output_base}.txt\""

    [[ "$SAVE_AUDIO" == "true" ]] && cmd+=" -sa"

    echo "🎙️ Starting Live Dictation... Press [Ctrl+C] to stop."
    echo "💾 Text output will be saved to: ${output_base}.txt"
    if [[ "$SAVE_AUDIO" == "true" ]]; then
        echo "🎧 Audio capture will be saved to: ${output_base}.txt.wav"
    fi

    eval "$cmd" 2>&1 | tee "${output_base}_raw_stream.log"
    echo "✅ Live dictation complete."
}

# Allow direct execution if not sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    live_dictate "$@"
fi