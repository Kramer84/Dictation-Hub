#!/bin/bash

function run_whisper_core() {
    local input_wav="$1"
    local config_file="$2"
    local output_base="$3"

    # 1. Load Configuration
    if [ -f "$config_file" ]; then
        source "$config_file"
    else
        echo "Error: Config file $config_file not found."
        return 1
    fi

    # 2. Resolve Paths
    local whisper_dir="${WHISPER_CPP_DIR:-$HOME/whisper.cpp}"
    local cli_executable="${whisper_dir}/build/bin/whisper-cli"
    local model_path="${whisper_dir}/models/ggml-${MODEL}.bin"

    if [ "$USE_QUANTIZED" == "true" ]; then
        local quant_path="${whisper_dir}/models/ggml-${MODEL}-q5_0.bin"
        if [ -f "$quant_path" ]; then
            model_path="$quant_path"
        fi
    fi

    # 3. Build Command
    local cmd="${cli_executable} -f \"$input_wav\" -m \"$model_path\""

    # Performance & Core
    cmd+=" -t ${THREADS} -p ${PROCESSORS} -bs ${BEAM_SIZE} --audio-ctx ${AUDIO_CTX}"

    # Thresholds
    cmd+=" --entropy-thold ${ENTROPY_THOLD} --logprob-thold ${LOGPROB_THOLD}"

    # Booleans
    [ "$TRANSLATE" == "true" ]     && cmd+=" --translate"
    [ "$NO_FALLBACK" == "true" ]   && cmd+=" --no-fallback"
    [ "$PRINT_SPECIAL" == "true" ] && cmd+=" --print-special"
    [ "$TINYDIARIZE" == "true" ]   && cmd+=" --tinydiarize"
    [ "$SUPPRESS_NST" == "true" ]  && cmd+=" --suppress-nst"
    [ "$NO_TIMESTAMPS" == "true" ] && cmd+=" --no-timestamps"

    # Prompt & Lang
    cmd+=" --language ${LANGUAGE}"
    cmd+=" --prompt \"${INITIAL_PROMPT}\""

    # Output
    cmd+=" --output-file \"${output_base}\" -ojf -otxt -np"

    # 4. Execute with Logging
    local log_file="${output_base}.log"
    # echo "   [Cmd] $cmd" > "$log_file" # Uncomment to debug the exact command string

    eval "$cmd" >> "$log_file" 2>&1
    local status=$?

    if [ $status -ne 0 ]; then
        echo "   [!] Whisper Failed (Code $status). See: $log_file"
    elif [ ! -f "${output_base}.json" ]; then
        echo "   [!] Whisper finished but no JSON produced. See: $log_file"
    fi
}
