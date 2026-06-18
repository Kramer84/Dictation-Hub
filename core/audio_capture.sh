#!/bin/bash
# core/audio_capture.sh

function audio_capture() {
    local output_file=""
    local normalize=false
    local remove_silence=false
    local highpass_filter=false

    while [[ "$#" -gt 0 ]]; do
        case $1 in
            --output) output_file="$2"; shift 2 ;;
            --normalize) normalize=true; shift ;;
            --remove-silence) remove_silence=true; shift ;;
            --highpass) highpass_filter=true; shift ;;
            *) echo "Unknown option: $1"; return 1 ;;
        esac
    done

    if [ -z "$output_file" ]; then
        echo "Error: --output file path is required."
        return 1
    fi

    mkdir -p "$(dirname "$output_file")"
    local temp_wav="${output_file}.tmp.wav"

    # Trap SIGINT so this wrapper survives Ctrl+C and proceeds to ffmpeg processing
    trap 'echo "[Audio Capture] Intercepted SIGINT. Stopping recording..."' SIGINT

    arecord -f cd -t wav "$temp_wav" &>/dev/null &
    local rec_pid=$!

    # Wait indefinitely for arecord to terminate (it will naturally die on Ctrl+C)
    wait $rec_pid 2>/dev/null || true

    # Reset trap to default
    trap - SIGINT

    echo "-> Processing audio format (16kHz, mono)..."
    local ffmpeg_cmd="ffmpeg -hide_banner -loglevel error -y -i \"$temp_wav\" -ar 16000 -ac 1 -c:a pcm_s16le"
    local filters=""

    if [ "$normalize" = true ]; then
        echo "   [Pending Implementation] Normalization requested."
    fi

    if [ -n "$filters" ]; then
        ffmpeg_cmd+=" -af \"$filters\""
    fi

    eval "$ffmpeg_cmd \"$output_file\""
    rm -f "$temp_wav"
    echo "✅ Audio saved to $output_file"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    audio_capture "$@"
fi