#!/bin/bash
# setup/fetch_models.sh

WHISPER_DIR="$HOME/whisper.cpp"
MANIFEST_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/models_manifest.txt"

if [ ! -d "$WHISPER_DIR/models" ]; then
    echo "❌ Error: whisper.cpp models directory not found at $WHISPER_DIR/models"
    echo "Run setup/install_whisper.sh first."
    exit 1
fi

if [ ! -f "$MANIFEST_FILE" ]; then
    echo "❌ Error: Manifest file not found at $MANIFEST_FILE"
    exit 1
fi

cd "$WHISPER_DIR/models" || exit 1

echo "========================================"
echo " Fetching Models from Manifest"
echo "========================================"

while IFS= read -r model || [ -n "$model" ]; do
    # Ignore empty lines and comments
    if [[ -z "$model" || "$model" == \#* ]]; then
        continue
    fi

    echo "-> Checking for model: $model"

    if [[ "$model" == *"silero"* ]]; then
        # Handle Silero VAD download separately if standard script doesn't cover it
        # Note: The download-ggml-model.sh script might not handle silero directly in all whisper.cpp versions.
        # Fallback to direct wget if needed.
        if [ ! -f "ggml-${model}.bin" ]; then
            echo "   Downloading Silero VAD..."
            wget -q --show-progress -O "ggml-${model}.bin" "https://github.com/ggerganov/whisper.cpp/raw/master/models/ggml-${model}.bin"
        else
            echo "   [Skip] $model already exists."
        fi
    else
        # Handle standard whisper models
        if [ ! -f "ggml-${model}.bin" ]; then
            bash ./download-ggml-model.sh "$model"
        else
            echo "   [Skip] $model already exists."
        fi
    fi

done < "$MANIFEST_FILE"

echo "✅ All models fetched."