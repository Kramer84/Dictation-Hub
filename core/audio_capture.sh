#!/bin/bash

# core/audio_capture.sh
# Handles audio recording and standardizes output to 16kHz mono WAV for Whisper.cpp.
# This is legacy, since we use live_dictate to save the audio directly in the correct format. However, this script can still be used for testing or as a standalone utility.

function audio_capture() {
    local output_file=""
    local normalize=false
    local remove_silence=false
    local highpass_filter=false

    # Strict parser for --flag syntax
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
    local raw_wav="${output_file}_raw.wav"

    echo "🎙️ Recording audio... Press [Enter] or [Ctrl+C] to stop."
    
    arecord -f cd -t wav "$raw_wav" &>/dev/null &
    local rec_pid=$!

    trap "kill $rec_pid 2>/dev/null; wait $rec_pid 2>/dev/null" SIGINT
    read -r
    
    kill $rec_pid 2>/dev/null
    wait $rec_pid 2>/dev/null
    trap - SIGINT

    echo "-> Processing audio format (16kHz, mono)..."
    local ffmpeg_cmd="ffmpeg -hide_banner -loglevel error -y -i \"$raw_wav\" -ar 16000 -ac 1 -c:a pcm_s16le"
    local filters=""

    if [ "$normalize" = true ]; then
        echo "   [Pending Implementation] Normalization requested."
    fi
    if [ "$remove_silence" = true ]; then
        echo "   [Pending Implementation] Silence removal requested."
    fi
    if [ "$highpass_filter" = true ]; then
         echo "   [Pending Implementation] Highpass filter requested."
    fi

    if [ -n "$filters" ]; then
        filters=${filters%,}
        ffmpeg_cmd+=" -af \"$filters\""
    fi

    eval "$ffmpeg_cmd \"$output_file\""
    rm -f "$raw_wav"
    echo "✅ Audio saved to $output_file"
}

# Allow direct execution if not sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    audio_capture "$@"
fi