#!/bin/bash

cd "$HOME/wifi-visualizer" || exit 1

sudo -v || exit 1

sudo "$HOME/wifi-visualizer/dist/Wi-Fi Visualizer.app/Contents/MacOS/Wi-Fi Visualizer"
