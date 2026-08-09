#!/bin/bash

cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
    echo "⚠️ Please run setup.command first."
    read -p "Press Enter to close..."
    exit 1
fi

echo "📡 Starting Wi-Fi Visualizer..."
echo ""

sudo -v

if [ $? -ne 0 ]; then
    echo "❌ Administrator permission was not granted."
    read -p "Press Enter to close..."
    exit 1
fi

.venv/bin/python dashboard.py

echo ""
echo "Wi-Fi Visualizer closed."
read -p "Press Enter to close..."
