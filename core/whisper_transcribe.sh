#!/bin/bash
# core/whisper_transcribe.sh

function whisper_transcribe() {
    local input_wav=""
    local config_env=""
    local output_base=""

    while [[ "$#" -gt 0 ]]; do
        case $1 in
            --input) input_wav="$2"; shift 2 ;;
            --config) config_env="$2"; shift 2 ;;
            --output) output_base="$2"; shift 2 ;;
            *) echo "Unknown option: $1"; return 1 ;;
        esac
    done

    if [[ -z "$input_wav" || ! -f "$input_wav" ]]; then
        echo "Error: A valid input audio file must be passed via --input <file.wav>"
        return 1
    fi

    if [[ -n "$config_env" && -f "$config_env" ]]; then
        source "$config_env"
    fi

    # --- Core Options ---
    : ${WHISPER_DIR:="$HOME/whisper.cpp"}
    : ${MODEL:="large-v3"}
    : ${THREADS:=4}
    : ${PROCESSORS:=1}

    # --- Audio Timing & Constraints ---
    : ${OFFSET_T:=0}
    : ${OFFSET_N:=0}
    : ${DURATION:=0}
    : ${MAX_CONTEXT:=-1}
    : ${MAX_LEN:=0}
    : ${SPLIT_ON_WORD:="false"}

    # --- Sampling & Thresholds ---
    : ${BEST_OF:=5}
    : ${BEAM_SIZE:=5}
    : ${AUDIO_CTX:=0}
    : ${WORD_THOLD:=0.01}
    : ${ENTROPY_THOLD:=2.40}
    : ${LOGPROB_THOLD:=-1.00}
    : ${NO_SPEECH_THOLD:=0.60}
    : ${TEMPERATURE:=0.00}
    : ${TEMPERATURE_INC:=0.20}

    # --- Modes & Behaviours ---
    : ${DEBUG_MODE:="false"}
    : ${TRANSLATE:="false"}
    : ${DIARIZE:="false"}
    : ${TINYDIARIZE:="false"}
    : ${NO_FALLBACK:="false"}

    # --- Output Formats (Toggled via "true") ---
    : ${OUTPUT_TXT:="false"}
    : ${OUTPUT_VTT:="false"}
    : ${OUTPUT_SRT:="false"}
    : ${OUTPUT_LRC:="false"}
    : ${OUTPUT_WORDS:="false"}
    : ${FONT_PATH:=""}
    : ${OUTPUT_CSV:="false"}
    : ${OUTPUT_JSON:="false"}
    : ${OUTPUT_JSON_FULL:="true"} # Kept true as per your original default setup

    # --- Printing Preference ---
    : ${NO_PRINTS:="true"}        # Kept true as per your original default setup
    : ${PRINT_SPECIAL:="false"}
    : ${PRINT_COLORS:="false"}
    : ${PRINT_PROGRESS:="false"}
    : ${NO_TIMESTAMPS:="false"}

    # --- Language Setup ---
    : ${LANGUAGE:="auto"}
    : ${DETECT_LANGUAGE:="false"}
    : ${INITIAL_PROMPT:=""}

    # --- Hardware & Acceleration ---
    : ${OV_E_DEVICE:="CPU"}
    : ${DTW_MODEL:=""}
    : ${LOG_SCORE:="false"}
    : ${NO_GPU:="false"}
    : ${FLASH_ATTN:="false"}

    # --- Suppressions & Grammars ---
    : ${SUPPRESS_NST:="true"}
    : ${SUPPRESS_REGEX:=""}
    : ${GRAMMAR:=""}
    : ${GRAMMAR_RULE:=""}
    : ${GRAMMAR_PENALTY:=100.0}

    # --- Voice Activity Detection (VAD) ---
    : ${USE_VAD:="false"}
    : ${VAD_MODEL:="silero-v6.2.0"}
    : ${VAD_THOLD:=0.60}
    : ${VAD_MIN_SPEECH:=100}
    : ${VAD_MIN_SILENCE:=100}
    : ${VAD_MAX_SPEECH:=30}
    : ${VAD_PAD:=50}
    : ${VAD_OVERLAP:=0.10}

    local cli_exec="${WHISPER_DIR}/build/bin/whisper-cli"
    local model_path="${WHISPER_DIR}/models/ggml-${MODEL}.bin"
    
    if [[ -n "$output_base" ]]; then
        output_base="${output_base%.*}"
        mkdir -p "$(dirname "$output_base")"
    else
        output_base="${input_wav%.*}"
    fi

    if [[ ! -x "$cli_exec" ]]; then
        echo "Error: whisper-cli executable not found at $cli_exec"
        return 1
    fi

    # Build Core Command
    local cmd="\"$cli_exec\" -f \"$input_wav\" -m \"$model_path\""
    cmd+=" -t $THREADS -p $PROCESSORS -bs $BEAM_SIZE -ac $AUDIO_CTX -mc $MAX_CONTEXT"
    cmd+=" -et $ENTROPY_THOLD -lpt $LOGPROB_THOLD -wt $WORD_THOLD"
    cmd+=" -l \"$LANGUAGE\""

    # Core Offsets & Lengths
    cmd+=" -ot $OFFSET_T -on $OFFSET_N -d $DURATION -ml $MAX_LEN -bo $BEST_OF"
    cmd+=" -nth $NO_SPEECH_THOLD --temperature $TEMPERATURE --temperature-inc $TEMPERATURE_INC"
    cmd+=" --ov-e-device \"$OV_E_DEVICE\" --grammar-penalty $GRAMMAR_PENALTY"

    # Simple Flag Evaluators
    [[ "$SPLIT_ON_WORD" == "true" ]]  && cmd+=" -sow"
    [[ "$DEBUG_MODE" == "true" ]]     && cmd+=" -debug"
    [[ "$TRANSLATE" == "true" ]]      && cmd+=" -tr"
    [[ "$DIARIZE" == "true" ]]        && cmd+=" -di"
    [[ "$TINYDIARIZE" == "true" ]]    && cmd+=" -tdrz"
    [[ "$NO_FALLBACK" == "true" ]]    && cmd+=" -nf"
    [[ "$NO_PRINTS" == "true" ]]      && cmd+=" -np"
    [[ "$PRINT_SPECIAL" == "true" ]]   && cmd+=" -ps"
    [[ "$PRINT_COLORS" == "true" ]]    && cmd+=" -pc"
    [[ "$PRINT_PROGRESS" == "true" ]]  && cmd+=" -pp"
    [[ "$NO_TIMESTAMPS" == "true" ]]  && cmd+=" -nt"
    [[ "$DETECT_LANGUAGE" == "true" ]] && cmd+=" -dl"
    [[ "$LOG_SCORE" == "true" ]]      && cmd+=" -ls"
    [[ "$NO_GPU" == "true" ]]         && cmd+=" -ng"
    [[ "$FLASH_ATTN" == "true" ]]     && cmd+=" -fa"
    [[ "$SUPPRESS_NST" == "true" ]]   && cmd+=" -sns"

    # Format Outputs
    [[ "$OUTPUT_TXT" == "true" ]]       && cmd+=" -otxt"
    [[ "$OUTPUT_VTT" == "true" ]]       && cmd+=" -ovtt"
    [[ "$OUTPUT_SRT" == "true" ]]       && cmd+=" -osrt"
    [[ "$OUTPUT_LRC" == "true" ]]       && cmd+=" -olrc"
    [[ "$OUTPUT_WORDS" == "true" ]]     && cmd+=" -owts"
    [[ "$OUTPUT_CSV" == "true" ]]       && cmd+=" -ocsv"
    [[ "$OUTPUT_JSON" == "true" ]]      && cmd+=" -oj"
    [[ "$OUTPUT_JSON_FULL" == "true" ]] && cmd+=" -ojf"

    # String & Value Guards
    [[ -n "$FONT_PATH" ]]      && cmd+=" -fp \"$FONT_PATH\""
    [[ -n "$INITIAL_PROMPT" ]] && cmd+=" --prompt \"$INITIAL_PROMPT\""
    [[ -n "$DTW_MODEL" ]]      && cmd+=" -dtw \"$DTW_MODEL\""
    [[ -n "$SUPPRESS_REGEX" ]] && cmd+=" --suppress-regex \"$SUPPRESS_REGEX\""
    [[ -n "$GRAMMAR" ]]        && cmd+=" --grammar \"$GRAMMAR\""
    [[ -n "$GRAMMAR_RULE" ]]   && cmd+=" --grammar-rule \"$GRAMMAR_RULE\""

    # Voice Activity Detection Logic
    if [[ "$USE_VAD" == "true" ]]; then
        local vad_model_path="${WHISPER_DIR}/models/ggml-${VAD_MODEL}.bin"
        if [[ -f "$vad_model_path" ]]; then
            cmd+=" --vad --vad-model \"$vad_model_path\""
            cmd+=" --vad-threshold $VAD_THOLD"
            cmd+=" --vad-min-speech-duration-ms $VAD_MIN_SPEECH"
            cmd+=" --vad-min-silence-duration-ms $VAD_MIN_SILENCE"
            cmd+=" --vad-max-speech-duration-s $VAD_MAX_SPEECH"
            cmd+=" --vad-speech-pad-ms $VAD_PAD"
            cmd+=" --vad-samples-overlap $VAD_OVERLAP"
        fi
    fi

    # Direct base filename target allocation
    cmd+=" -of \"$output_base\""

    echo "-> Executing Whisper Inference..."
    eval "$cmd"

    if [[ $? -eq 0 ]]; then
        echo "✅ Transcription process complete using target: ${output_base}"
    else
        echo "❌ Whisper execution failed."
        return 1
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    whisper_transcribe "$@"
fi