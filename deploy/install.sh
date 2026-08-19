#!/bin/bash
# Installs the udev rule + systemd services so the pipeline runs
# automatically when an SD card or USB drive is inserted, and the web
# interface starts on boot. Run this ON THE RASPBERRY PI, not on a dev
# machine -- it writes to /etc/udev and /etc/systemd.
#
# Usage: cd analyze-and-backup && sudo bash deploy/install.sh

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Run this with sudo: sudo bash deploy/install.sh"
  exit 1
fi

# Resolve the project directory as the parent of this script's location,
# and the user who invoked sudo (falls back to 'pi' if run as raw root).
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$DEPLOY_DIR")"
PROJECT_USER="${SUDO_USER:-pi}"

echo "Project directory: $PROJECT_DIR"
echo "Running as:         $PROJECT_USER"

if [ ! -x "$PROJECT_DIR/.venv/bin/python3" ]; then
  echo "WARNING: $PROJECT_DIR/.venv/bin/python3 not found."
  echo "Set up the virtual environment first (python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt)"
  exit 1
fi

echo "Installing udev rule..."
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__PROJECT_USER__|$PROJECT_USER|g" \
  "$DEPLOY_DIR/99-analyze-and-backup.rules" > /etc/udev/rules.d/99-analyze-and-backup.rules
udevadm control --reload

echo "Installing systemd services..."
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__PROJECT_USER__|$PROJECT_USER|g" \
  "$DEPLOY_DIR/analyze-and-backup@.service" > /etc/systemd/system/analyze-and-backup@.service
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__PROJECT_USER__|$PROJECT_USER|g" \
  "$DEPLOY_DIR/analyze-and-backup-web.service" > /etc/systemd/system/analyze-and-backup-web.service

systemctl daemon-reload
systemctl enable --now analyze-and-backup-web.service

echo ""
echo "Done."
echo "  - Web interface: running now, will also start on every boot."
echo "  - Card processing: will start automatically next time a USB drive"
echo "    or SD card (via USB reader) is inserted."
echo ""
echo "Check the web interface is up:   systemctl status analyze-and-backup-web.service"
echo "Watch it process a card:         journalctl -u 'analyze-and-backup@*' -f"
echo ""
echo "Next: set a stable hostname (sudo raspi-config -> System Options -> Hostname)"
echo "so http://<hostname>.local:5000 doesn't change, then regenerate the QR sign:"
echo "  python3 generate_qr.py --url http://<hostname>.local:5000"
