#!/bin/bash

cd "$(dirname "$0")" || exit 1

echo "📡 Wi-Fi Visualizer Setup"
echo "========================"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Python 3 was not found."
    read -p "Press Enter to close..."
    exit 1
fi

echo "🐍 Creating Python environment..."
python3 -m venv .venv

echo "📦 Installing dependencies..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo ""
echo "✅ Setup complete!"
echo ""
read -p "Press Enter to close..."
