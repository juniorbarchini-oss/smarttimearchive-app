#!/bin/bash
# Move to script directory
cd "$(dirname "$0")"

# Create venv if not exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv and install dependencies
echo "Activating virtual environment..."
source venv/bin/activate

echo "Checking / Installing PySide6..."
pip install --upgrade pip
pip install PySide6

echo "Launching SmartTimeArchive..."
python3 main.py
