#!/usr/bin/env bash
# Adhan Playback System — Raspberry Pi Setup Script
# Run this on a freshly flashed Pi OS Lite (Bookworm 64-bit)
set -euo pipefail

INSTALL_DIR="/opt/adhan"

echo "=== Adhan Playback System Setup ==="

# 1. Install system dependencies
echo "Installing system packages..."
sudo apt update
sudo apt install -y python3-pip python3-venv mpv

# 2. Create install directory
echo "Setting up ${INSTALL_DIR}..."
sudo mkdir -p "${INSTALL_DIR}"
sudo chown "$(whoami):$(whoami)" "${INSTALL_DIR}"

# 3. Copy project files (assumes script is run from project root)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

echo "Copying project files..."
cp -r "${PROJECT_DIR}/src" "${INSTALL_DIR}/"
cp -r "${PROJECT_DIR}/audio" "${INSTALL_DIR}/"
cp "${PROJECT_DIR}/requirements.txt" "${INSTALL_DIR}/"
cp "${PROJECT_DIR}/config.yaml" "${INSTALL_DIR}/"

# Create logs directory
mkdir -p "${INSTALL_DIR}/logs"

# 4. Create virtual environment and install dependencies
echo "Setting up Python virtual environment..."
cd "${INSTALL_DIR}"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 5. Install systemd service
echo "Installing systemd service..."
sudo cp "${SCRIPT_DIR}/adhan.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable adhan

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit ${INSTALL_DIR}/config.yaml with your location"
echo "  2. Place adhan MP3 files in ${INSTALL_DIR}/audio/"
echo "  3. Configure audio output: sudo raspi-config → System → Audio → Headphones"
echo "  4. Start the service: sudo systemctl start adhan"
echo "  5. Check logs: journalctl -u adhan -f"
