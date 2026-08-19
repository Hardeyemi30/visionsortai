# Deploying to the Raspberry Pi

Turns the pipeline into a hands-off appliance: insert a card, it processes
automatically, and a printed QR code sends the user to a web page showing
the results. Run all of this **on the Pi**, not your dev machine.

## 1. Set up the project on the Pi

```bash
git clone <your repo>   # or copy the project folder over
cd analyze-and-backup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt install libimage-exiftool-perl   # exiftool
cp .env.example .env                       # fill in thresholds / Azure creds later
```

## 2. Install the auto-run service + web interface

```bash
sudo bash deploy/install.sh
```

This installs:
- `99-analyze-and-backup.rules` (udev) -- detects when a USB drive, an SD
  card via USB reader, or a card in the SPI MH-SD module (once its overlay
  is installed -- see step 2.5) is inserted
- `analyze-and-backup@.service` (systemd, templated) -- runs `cli.py`
  against whatever was just inserted, mock agent + local backup by default
  until Azure is configured
- `analyze-and-backup-web.service` (systemd) -- runs `webapp.py` on boot,
  restarts automatically if it crashes

There's no physical status box or button in this project -- shut the Pi
down via SSH as usual (`sudo shutdown -h now`).

## 2.5. (Optional) SPI microSD card module

Only needed if you're using the MH-SD SPI breakout board instead of (or in
addition to) a USB card reader -- see `deploy/mmc-spi-overlay/README.md`
for wiring and overlay installation. Do this **before** step 2 above so the
udev rule's mmcblk matching has something to actually detect.

## 3. Give the Pi a stable hostname

IP addresses change; a hostname doesn't. Raspberry Pi OS ships with
`avahi-daemon`, which makes the Pi reachable at `<hostname>.local` on any
network that supports mDNS (which is most home/office networks, but not the
open internet -- the scanning device has to be on the same local network).

```bash
sudo raspi-config    # System Options > Hostname, e.g. "analyze-and-backup"
```

## 4. Print the QR sign

```bash
python3 generate_qr.py --url http://analyze-and-backup.local:5000
```

Print `qr_sign.png` once and stick it near the Pi. Only regenerate it if the
hostname changes.

## Checking it's working

```bash
systemctl status analyze-and-backup-web.service      # web interface up?
journalctl -u 'analyze-and-backup@*' -f               # watch card processing live
```

Insert a USB drive with a few test photos on it, watch the journalctl
output, then scan the QR sign (or visit the URL manually) to see the
results.
