#!/bin/bash
set -e

echo "==> Installing Python and pip..."
apt-get update -qq
apt-get install -y python3-pip python-is-python3

echo "==> Installing dependencies..."
pip3 install -r requirements.txt

echo "==> Starting Agent Team on http://localhost:7860"
echo "    (Press Ctrl+C to stop)"
echo ""
python3 web_server.py
