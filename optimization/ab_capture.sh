#!/usr/bin/env bash
# A/B test of the two capture paths on the *same* speech:
#   cd        : arecord -f cd (44.1 kHz stereo), converted to 16 kHz mono by ffmpeg (current default)
#   direct16k : arecord -r 16000 -c 1 (resampled by PipeWire/ALSA)
# Both run at the same time on the default source, then both are transcribed.
# Usage: optimization/ab_capture.sh [output_dir]
set -euo pipefail

OUT=${1:-$(mktemp -d /tmp/ab_capture.XXXX)}
mkdir -p "$OUT"
CLI=${WHISPER_CLI:-$HOME/whisper.cpp/build/bin/whisper-cli}
MODEL=${MODEL:-$HOME/whisper.cpp/models/ggml-large-v3-turbo-q5_0.bin}

arecord -q -f cd -t wav "$OUT/cd.wav" &
A=$!
arecord -q -f S16_LE -r 16000 -c 1 -t wav "$OUT/direct16k.wav" &
B=$!
trap 'kill -INT $A $B 2>/dev/null || true' INT
read -r -p "Recording with both paths. Speak normally, then press Enter to stop. " _ || true
kill -INT $A $B 2>/dev/null || true
wait $A $B 2>/dev/null || true
trap - INT

ffmpeg -hide_banner -loglevel error -y -i "$OUT/cd.wav" -ar 16000 -ac 1 -c:a pcm_s16le "$OUT/cd_16k.wav"

for f in cd_16k direct16k; do
    level=$(ffmpeg -hide_banner -nostats -i "$OUT/$f.wav" -af volumedetect -f null - 2>&1 \
        | awk '/mean_volume/ {m=$5} /max_volume/ {x=$5} END {print "mean " m " dB, peak " x " dB"}')
    echo
    echo "=== $f ($level)"
    "$CLI" -f "$OUT/$f.wav" -m "$MODEL" -l auto -nt -np 2>/dev/null
done
echo
echo "Files kept in $OUT"
