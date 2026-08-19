#!/usr/bin/env python3
"""
Quick hardware smoke test for the bar graph.
(The 8x8 matrix + 74HC595 driver, and the physical safe-shutdown button,
have both been dropped from this project -- see pi_status_box.py's module
docstring. The bar graph now covers the "job in progress" indicator role
too, via BarGraphPulser.)

pi_status_box.py imports analyze_and_backup.config, so this needs to
run from the analyze-and-backup project root with its .venv active (same
place cli.py lives):
    cd analyze-and-backup
    source .venv/bin/activate
    python3 test_hardware.py
"""

import subprocess
import time

from gpiozero import LEDBarGraph

from pi_status_box import BAR_GRAPH_PINS


def check_undervoltage():
    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, check=True
        ).stdout.strip()
        print(f"[power]  {out}  (want: throttled=0x0)")
    except Exception as e:
        print(f"[power]  couldn't run vcgencmd: {e}")


def check_spi():
    import os

    # Only /dev/spidev0.0 (CE0) is expected to be absent -- the
    # mmc-spi-mhsd overlay's fragment@0 disables just that one so the
    # MH-SD module's mmc-spi-slot node can claim CE0 instead. CE1
    # (/dev/spidev0.1) is intentionally left alone and stays available
    # for anything else you wire there later -- its presence is normal,
    # not an error. What actually confirms SPI0 + the MH-SD module are
    # working is a card block device showing up once a card is inserted.
    if os.path.exists("/dev/spidev0.0"):
        print("[spi]    WARNING: /dev/spidev0.0 still present -- mmc-spi-mhsd overlay "
              "may not be loaded (expected CE0 to be disabled)")
    else:
        print("[spi]    spidev0.0 (CE0) absent, as expected -- overlay handed it to the MH-SD module")

    # mmcblk0 is the Pi's own boot SD card -- exclude it, we only care
    # about a card inserted into the MH-SD module (mmcblk1, mmcblk3, ...).
    mmcblks = sorted(p for p in os.listdir("/dev")
                      if p.startswith("mmcblk") and not p.startswith("mmcblk0"))
    print(f"[spi]    MH-SD card block devices: {mmcblks or 'none (insert a card to check)'}")


def test_bargraph():
    print("[bargraph] filling up 0 -> 10 segments over 2s "
          "(nothing will happen if it isn't wired yet, that's fine)...")
    # active_high=False: common-anode wiring, GPIOs on the cathodes -- see
    # pi_status_box_wiring_final.md and the matching note in pi_status_box.py.
    bar_graph = LEDBarGraph(*BAR_GRAPH_PINS, pwm=False, active_high=False)
    for i in range(11):
        bar_graph.value = i / 10
        time.sleep(0.2)

    print("[bargraph] running the 'job busy' chase animation for 3s...")
    n = len(bar_graph)
    sequence = list(range(n)) + list(range(n - 2, 0, -1))
    end = time.time() + 3
    while time.time() < end:
        for i in sequence:
            bar_graph.off()
            bar_graph[i].on()
            time.sleep(0.08)
            if time.time() >= end:
                break

    print("[bargraph] flashing 'safe to unplug' pattern...")
    for _ in range(3):
        bar_graph.on()
        time.sleep(0.2)
        bar_graph.off()
        time.sleep(0.2)

    bar_graph.off()
    print("[bargraph] done, cleared.")


if __name__ == "__main__":
    check_undervoltage()
    check_spi()
    test_bargraph()
    print("Smoke test complete.")
