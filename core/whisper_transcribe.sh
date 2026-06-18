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

    : ${WHISPER_DIR:="$HOME/whisper.cpp"}
    : ${MODEL:="large-v3"}
    : ${THREADS:=8}
    : ${PROCESSORS:=1}
    : ${BEAM_SIZE:=5}          
    : ${AUDIO_CTX:=0}          
    : ${ENTROPY_THOLD:=2.40}   
    : ${LOGPROB_THOLD:=-1.00}  
    : ${WORD_THOLD:=0.01}
    : ${MAX_CONTEXT:=-1}       
    : ${USE_VAD:="false"}
    : ${VAD_MODEL:="silero-v6.2.0"}
    : ${VAD_THOLD:=0.60}
    : ${VAD_MIN_SPEECH:=100}   
    : ${VAD_MIN_SILENCE:=100}  
    : ${VAD_MAX_SPEECH:=30}    
    : ${VAD_PAD:=50}           
    : ${VAD_OVERLAP:=0.10}     
    : ${LANGUAGE:="auto"}
    : ${INITIAL_PROMPT:=""}
    : ${TRANSLATE:="false"}    
    : ${NO_FALLBACK:="false"}  
    : ${SUPPRESS_NST:="true"}  
    : ${NO_TIMESTAMPS:="false"}
    : ${TINYDIARIZE:="false"}

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

    local cmd="\"$cli_exec\" -f \"$input_wav\" -m \"$model_path\""
    cmd+=" -t $THREADS -p $PROCESSORS -bs $BEAM_SIZE -ac $AUDIO_CTX -mc $MAX_CONTEXT"
    cmd+=" -et $ENTROPY_THOLD -lpt $LOGPROB_THOLD -wt $WORD_THOLD"
    cmd+=" -l \"$LANGUAGE\""

    [[ -n "$INITIAL_PROMPT" ]] && cmd+=" --prompt \"$INITIAL_PROMPT\""
    [[ "$TRANSLATE" == "true" ]]     && cmd+=" --translate"
    [[ "$NO_FALLBACK" == "true" ]]   && cmd+=" --no-fallback"
    [[ "$SUPPRESS_NST" == "true" ]]  && cmd+=" --suppress-nst"
    [[ "$NO_TIMESTAMPS" == "true" ]] && cmd+=" --no-timestamps"
    [[ "$TINYDIARIZE" == "true" ]]   && cmd+=" --tinydiarize"

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

    cmd+=" -ojf -otxt -np -of \"$output_base\""

    echo "-> Executing Whisper Inference..."
    eval "$cmd"

    if [[ $? -eq 0 && -f "${output_base}.json" ]]; then
        echo "✅ Transcription complete: ${output_base}.json"
    else
        echo "❌ Whisper execution failed or JSON not produced."
        return 1
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    whisper_transcribe "$@"
fi