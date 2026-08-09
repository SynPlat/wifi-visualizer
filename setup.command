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

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

echo ""
echo "📦 Installing dependencies..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo ""
echo "🔨 Installing PyInstaller..."
.venv/bin/python -m pip install pyinstaller

echo ""
echo "🏗️ Building Wi-Fi Visualizer.app..."

rm -rf build dist

.venv/bin/pyinstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "Wi-Fi Visualizer" \
    dashboard.py

echo ""
echo "✅ Setup complete!"
echo ""
echo "Run the dashboard with:"
echo "sudo -k"
echo "./launch_wifi_visualizer.sh"
echo ""

read -p "Press Enter to close..."
