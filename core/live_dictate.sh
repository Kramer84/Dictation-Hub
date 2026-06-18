#!/bin/bash
# core/live_dictate.sh

function live_dictate() {
    local output_base=""
    local config_env=""

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

    if [[ -n "$config_env" && -f "$config_env" ]]; then
        source "$config_env"
    fi

    : ${WHISPER_DIR:="$HOME/whisper.cpp"}
    : ${MODEL:="small"}
    : ${THREADS:=8}
    : ${STEP_MS:=0}           
    : ${LENGTH_MS:=10000}     
    : ${VAD_THOLD:=0.60}      
    : ${LANGUAGE:="auto"}
    : ${SAVE_AUDIO:="false"}  

    local cli_exec="${WHISPER_DIR}/build/bin/whisper-stream"
    local model_path="${WHISPER_DIR}/models/ggml-${MODEL}.bin"

    if [[ ! -x "$cli_exec" ]]; then
        echo "Error: whisper-stream not found at $cli_exec."
        return 1
    fi

    # Fixed suffix duplication: strip any extensions provided by the router 
    # since whisper-stream modifies the file output string.
    local clean_output_base="${output_base%.*}"

    local cmd="\"$cli_exec\" -m \"$model_path\" -t $THREADS"
    cmd+=" --step $STEP_MS --length $LENGTH_MS -vth $VAD_THOLD"
    cmd+=" -l \"$LANGUAGE\""
    
    # Send output to exact file requested by routing
    cmd+=" -f \"$clean_output_base\""

    [[ "$SAVE_AUDIO" == "true" ]] && cmd+=" -sa"

    echo "🎙️ Starting Live Dictation... Press [Ctrl+C] to stop."
    echo "💾 Text output will be saved to: ${clean_output_base}.txt"

    eval "$cmd" 2>&1 | tee "${clean_output_base}_raw_stream.log"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    live_dictate "$@"
fi