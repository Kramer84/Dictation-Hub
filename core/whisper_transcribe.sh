function whisper_transcribe() {
    local model="large-v3"
    local use_quantized=true
    local threads=8
    local processors=1
    local audio_ctx=0
    local translate=false # To english
    local no_fallback=false
    local print_special=false
    local language="auto"
    local output_file=""
    local tinydiarize=false
    local flash_attn=false
    local print_colors=true
    local suppress_nst=true
    local no_timestamps=false # Was True
    local no_prints=false
    local beam_size=5
    # 1. Static prompt: Sets the tone (technical, complete sentences).
    local initial_prompt="Transcribe exactly what is said, including stutters, hesitations, and corrections. Do not summarize. Technical context."

    # Parse options
    while [[ "$#" -gt 0 ]]; do
        case $1 in
            --model)
                if [[ "$2" =~ ^(tiny.en|base.en|small.en|medium.en|large|large-v3)$ ]]; then
                    model="$2"
                else
                    echo "Warning: Invalid model specified. Falling back to 'base'."
                fi
                shift 2
                ;;
            --use-quantized)
                use_quantized=true
                shift
                ;;
            --threads)
                threads="$2"
                shift 2
                ;;
            --processors)
                processors="$2"
                shift 2
                ;;
            --audio-ctx)
                audio_ctx="$2"
                shift 2
                ;;
            --translate)
                translate=true
                shift
                ;;
            --no-fallback)
                no_fallback=true
                shift
                ;;
            --print-special)
                print_special=true
                shift
                ;;
            --language)
                language="$2"
                shift 2
                ;;
            --output-file)
                output_file="$2"
                shift 2
                ;;
            --tinydiarize)
                tinydiarize=true
                shift
                ;;
            --flash-attn)
                flash_attn=true
                shift
                ;;
            --print-colors)
                print_colors=true
                shift
                ;;
            --suppress-nst)
                suppress_nst=true
                shift
                ;;
            --no-timestamps)
                no_timestamps=true
                shift
                ;;
            --no-prints)
                no_prints=true
                shift
                ;;
            *)
                echo "Unknown option: $1"
                shift
                ;;
        esac
    done

    # --- PATH CONFIGURATION ---
    # Use the variable from .config.env, or fallback to default
    local whisper_dir="${WHISPER_CPP_DIR:-$HOME/whisper.cpp}"
    local cli_executable="${whisper_dir}/build/bin/whisper-cli"
    local model_path="${whisper_dir}/models/ggml-${model}.bin"

    if [ "$use_quantized" = true ]; then
        local quantized_model_path="${whisper_dir}/models/ggml-${model}-q5_0.bin"
        if [ -f "$quantized_model_path" ]; then
            model_path="$quantized_model_path"
        else
            echo "Warning: Quantized model not found. Using the standard model."
        fi
    fi

    # Check if the CLI executable exists
    if [ ! -f "${cli_executable}" ]; then
        echo "Error: ${cli_executable} executable not found."
        echo "Please check your WHISPER_CPP_DIR in .config.env"
        return 1
    fi

    # Check if an output file path is provided
    if [ -z "$output_file" ]; then
        # Create the directory if it doesn't exist
        mkdir -p "/home/${USER}/.whisper_transcriptions"
        # Create a file with a basic name and a timestamp
        timestamp=$(date +"%Y%m%d_%H%M%S")
        output_file="/home/${USER}/.whisper_transcriptions/transcription_${timestamp}/transcription"
    fi

    # Directory structure
    base_folder="/home/${USER}/.whisper_transcriptions"
    sub_folder="$(dirname "$output_file")"
    mkdir -p "$sub_folder"

    # Audio file paths
    wav_file="${sub_folder}/recording_raw.wav"
    #clean_wav_file="${sub_folder}/recording.wav"
    mp3_file="${sub_folder}/recording.mp3"

    # Record the audio in the background
    echo "Recording audio... Press Enter or Ctrl+C to stop."
    arecord -f cd -t wav "$wav_file" &
    record_pid=$!

    # Wait for Enter key or Ctrl+C to stop recording
    # Both Enter and Ctrl+C will be treated the same, stopping the recording and saving it
    trap "echo 'Recording stopped.'; kill $record_pid; wait $record_pid 2>/dev/null" SIGINT

    # Wait for input (Enter or Ctrl+C will both stop recording)
    read -r
    echo "Recording saved to $wav_file."

    # Stop the recording process if it's still running
    kill $record_pid 2>/dev/null
    wait $record_pid 2>/dev/null

    # Remove the trap so Ctrl+C works normally again
    trap - SIGINT

    # DEPRECEATED : Remove silence from the audio # echo "Removing silence from the audio..." # sox "$wav_file" "$clean_wav_file" silence -l 1 0.01 1% -1 3.0 1%

    # Convert the audio to the required format
    echo "Converting audio to WAV format..."
    ffmpeg -hide_banner -loglevel error -i "$wav_file" -ar 16000 -ac 1 -c:a pcm_s16le "${wav_file%.wav}_16bit.wav"
    wav_file="${wav_file%.wav}_16bit.wav"

    # --- TRANSCRIPTION ---
    # Build the Whisper transcription command
    cmd="${cli_executable} -f \"$wav_file\" -m \"$model_path\" -t ${threads} -p ${processors} --audio-ctx ${audio_ctx} -bs ${beam_size}"

    # ACCURACY TUNING (The Fix)
    # --entropy-thold: Default is 2.4. Lowering to 2.2 makes it stricter (less hallucination, maybe more dropped words).
    # --logprob-thold: Default -1.0. Raising to -0.8 rejects low-confidence guesses.
    cmd+=" --prompt \"${initial_prompt}\""
    cmd+=" --entropy-thold 2.4 --logprob-thold -1.0"

    # Add optional flags to the command
    [ "$translate" = true ] && cmd+=" --translate"
    [ "$no_fallback" = true ] && cmd+=" --no-fallback"
    [ "$print_special" = true ] && cmd+=" --print-special"
    [ "$tinydiarize" = true ] && cmd+=" --tinydiarize"
    [ "$flash_attn" = true ] && cmd+=" --flash-attn"
    [ "$print_colors" = true ] && cmd+=" --print-colors"
    [ "$suppress_nst" = true ] && cmd+=" --suppress-nst"
    [ "$no_timestamps" = true ] && cmd+=" --no-timestamps"
    [ "$no_prints" = true ] && cmd+=" --no-prints"

    # Add language and output file
    cmd+=" --output-file \"$output_file\" --language ${language} -ojf -otxt -np"

    # Execute the Whisper transcription command
    eval "$cmd"

    echo "Transcription saved to ${output_file}"

    # Convert the WAV file back to MP3
    echo "Converting WAV file back to MP3..."
    ffmpeg -hide_banner -loglevel error -i "$wav_file" "$mp3_file"

    echo "Audio file saved to ${mp3_file}"
    echo "Transcription saved to ${output_file}"

    # --- POST-PROCESSING ---
    # Determine which python to use (default to 'python3' if config var is empty)
    local python_cmd="${PYTHON_CORE:-python3}"

    if [ -f "$BASH_PROFILE_DIR/modules/speech2text/reconstruct_transcription.py" ]; then
        echo "Running cleanup using: $python_cmd"
        "$python_cmd" "$BASH_PROFILE_DIR/modules/speech2text/reconstruct_transcription.py" "${output_file}.json" "${output_file}_cleaned.txt"
    else
        echo "Error: Could not find reconstruct_transcription.py at $BASH_PROFILE_DIR/modules/speech2text/"
    fi

    # --- SMART DISPLAY ---
    echo -e "\n===== Cleaned Transcription =====\n"

    # Define arguments for a "Copy-Paste Friendly" mode:
    # -P            : Never page (keeps text in history, no scrolling buffer)
    # -p            : Plain style (no line numbers or grid borders)
    # -l md         : Force language to Markdown (adds color to a .txt file)
    BAT_ARGS="-P -p -l md"

    # 1. Check for 'batcat' (Ubuntu/Debian)
    if command -v batcat &> /dev/null; then
        batcat $BAT_ARGS "${output_file}_cleaned.txt"

    # 2. Check for 'bat' (and ensure it's not Bacula)
    elif command -v bat &> /dev/null; then
        if bat --version 2>&1 | grep -q "^bat "; then
            bat $BAT_ARGS "${output_file}_cleaned.txt"
        else
            cat "${output_file}_cleaned.txt"
        fi

    # 3. Fallback
    else
        cat "${output_file}_cleaned.txt"
    fi

    echo -e "\n"
}
