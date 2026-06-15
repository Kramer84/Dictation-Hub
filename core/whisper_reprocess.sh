function whisper_reprocess() {
    local input_wav="$1"
    if [ -z "$input_wav" ]; then echo "Usage: <wav_file>"; return 1; fi
    shift

    # --- CONFIGURATION ---
    local model="large-v3"

    # CRITICAL CHANGE 1: BEAM SIZE 2 (Default is 5)
    # This prevents the model from "overthinking" and getting stuck in loops.
    # It acts more like a greedy transcriber (hear -> write -> move on).
    local beam_size=2

    local whisper_dir="${WHISPER_CPP_DIR:-$HOME/whisper.cpp}"
    local cli_executable="${whisper_dir}/build/bin/whisper-cli"
    local model_path="${whisper_dir}/models/ggml-${model}.bin"
    if [ -f "${whisper_dir}/models/ggml-${model}-q5_0.bin" ]; then
        model_path="${whisper_dir}/models/ggml-${model}-q5_0.bin"
    fi

    local output_file="$(dirname "$input_wav")/$(basename "$input_wav" .wav)_reprocessed"

    # Clean old run
    rm -f "${output_file}.json" "${output_file}.txt" "${output_file}_cleaned.txt"

    echo "Reprocessing [Low-Beam Strategy]: $input_wav"

    # --- COMMAND ---
    # We REMOVED --vad and -mc because they caused your crash.
    local cmd="${cli_executable} -f \"$input_wav\" -m \"$model_path\""
    cmd+=" -t 8 -p 1"

    # 1. LOW BEAM SIZE (The Loop Killer substitute)
    cmd+=" -bs ${beam_size}"

    # 2. HIGH SENSITIVITY (The "Missing Ideas" Fix)
    # We keep these loose so it writes down everything it hears.
    cmd+=" --entropy-thold 2.8 --logprob-thold -2.0"

    # 3. STANDARD
    cmd+=" --no-fallback --language en"
    cmd+=" --prompt \"Transcribe exactly.\""
    cmd+=" --output-file \"$output_file\" -ojf -otxt -np"

    echo -e "Executing:\n$cmd\n"
    eval "$cmd"

    # --- CLEANUP ---
    local python_cmd="${PYTHON_CORE:-python3}"
    local clean_script="$BASH_PROFILE_DIR/modules/speech2text/reconstruct_transcription.py"

    if [ -f "${output_file}.json" ]; then
        echo "Running cleanup..."
        "$python_cmd" "$clean_script" "${output_file}.json" "${output_file}_cleaned.txt"

        echo -e "\n===== Output =====\n"
        if command -v batcat &> /dev/null; then batcat -P -p -l md "${output_file}_cleaned.txt"
        else cat "${output_file}_cleaned.txt"; fi
    else
        echo "Error: Whisper still failed. The audio file might be corrupt or the binary is unstable."
    fi
}
