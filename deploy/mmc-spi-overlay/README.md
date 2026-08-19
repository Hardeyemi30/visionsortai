# SPI microSD card overlay

Binds `mmc_spi` to the MH-SD module wired to SPI0 CE0, so it shows up as a
normal block device (`/dev/mmcblk1`, typically) that the udev rule and
`cli.py` can work with, the same as a USB-attached card.

I built this from the mainline kernel's `mmc-spi-slot` binding docs and
Raspberry Pi's own `spi0-Ncs` overlay pattern (verified: it compiles clean
with `dtc`, no warnings) -- but I could not find a confirmed working
example for this exact board to copy from, so treat the first boot as a
test, not a sure thing.

## Install (on the Pi)

```bash
sudo apt install device-tree-compiler   # if not already installed
cd deploy/mmc-spi-overlay
dtc -@ -I dts -O dtb -o mmc-spi-mhsd.dtbo mmc-spi-mhsd-overlay.dts
sudo cp mmc-spi-mhsd.dtbo /boot/firmware/overlays/   # Bookworm path
# (older Raspberry Pi OS: /boot/overlays/ instead)
```

Add to `/boot/firmware/config.txt` (or `/boot/config.txt` on older OS):

```
dtparam=spi=on
dtoverlay=mmc-spi-mhsd
```

Reboot, then check:

```bash
dmesg | grep -iE "mmc|spi"
ls /dev/mmcblk*
```

## If it doesn't show up

Paste the `dmesg` output back and we'll adjust. Likely failure points, in
rough order of likelihood:
- Wiring issue (double-check against `pi_status_box_wiring.md` section 1)
- The `spidev0` vs our `mhsd@0` chip-select handoff not behaving as
  expected on your specific kernel version
- SPI clock too fast for the breadboard wiring (already set conservatively
  to 8MHz here, but worth trying even lower, e.g. 4000000, if you see
  CRC/timeout errors in dmesg rather than no device at all)
