#!/bin/bash
# setup/install_whisper.sh

WHISPER_DIR="$HOME/whisper.cpp"

echo "========================================"
echo " Setting up whisper.cpp"
echo "========================================"

if [ -d "$WHISPER_DIR" ]; then
    echo "[Info] whisper.cpp already exists. Pulling latest changes..."
    cd "$WHISPER_DIR" || exit 1
    git pull
else
    echo "[Info] Cloning whisper.cpp repository..."
    git clone https://github.com/ggerganov/whisper.cpp.git "$WHISPER_DIR"
    cd "$WHISPER_DIR" || exit 1
fi

echo "[Info] Cleaning previous builds..."
make clean

echo "[Info] Compiling with GPU support (GGML_CUDA=1)..."
# Change GGML_CUDA=1 to your specific hardware flag if not using Nvidia
make GGML_CUDA=1 -j$(nproc)

if [ $? -eq 0 ]; then
    echo "✅ whisper.cpp compiled successfully."
else
    echo "❌ Compilation failed."
    exit 1
fi