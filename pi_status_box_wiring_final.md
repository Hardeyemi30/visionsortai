# Pi Status Box — Final Wiring Reference (matrix + button both dropped)

The 8x8 LED matrix + 2x74HC595 shift-register driver, and the physical
safe-shutdown push button, have both been **dropped from this project** —
the matrix was too fiddly to get wired/soldered reliably, and the button
never gave a clean, reliable press reading across two different physical
buttons and every leg combination (almost certainly a bad breadboard
section, not worth the remaining time to keep chasing). The bar graph
covers the "job in progress" indicator role on its own (see
`pi_status_box.py`'s `BarGraphPulser`): it pulses back and forth while a
card is processing, then flashes solid on (success) or blinks (failure)
briefly when done, then goes back to showing readiness. Shutting the Pi
down safely is just a normal SSH session: `sudo shutdown -h now`.

**This means the two 74HC595 chips, the matrix module, and the push
button are no longer needed anywhere in this project.** Only two physical
peripherals remain, both confirmed working on hardware: the MH-SD SPI
card reader and the 10-segment LED bar graph. This supersedes
`pi_status_box_wiring.md` (1-chip), `pi_status_box_wiring_2chip.md`
(2-chip), and the button section of earlier revisions of this file —
none of that is needed anymore.

---

## 1. GPIO pin budget (14 of 26 used)

Dropping the matrix and the button frees up even more headroom — full
serial console, I2C, and plenty of GPIO left for anything else you want
to add later.

| GPIO (BCM) | Physical pin | Assigned to |
|---|---|---|
| GPIO18 | 12 | **Bar graph** — segment 10 |
| GPIO10 | 19 | **MH-SD** — MOSI (hardware SPI0) |
| GPIO9  | 21 | **MH-SD** — MISO (hardware SPI0) |
| GPIO11 | 23 | **MH-SD** — SCLK (hardware SPI0) |
| GPIO8  | 24 | **MH-SD** — CE0 (hardware SPI0) |
| GPIO5  | 29 | **Bar graph** — segment 1 |
| GPIO6  | 31 | **Bar graph** — segment 2 |
| GPIO12 | 32 | **Bar graph** — segment 3 |
| GPIO13 | 33 | **Bar graph** — segment 4 |
| GPIO19 | 35 | **Bar graph** — segment 5 |
| GPIO16 | 36 | **Bar graph** — segment 6 |
| GPIO26 | 37 | **Bar graph** — segment 7 |
| GPIO20 | 38 | **Bar graph** — segment 8 |
| GPIO21 | 40 | **Bar graph** — segment 9 |

Everything else on the header (GPIO2, 3, 4, 7, 14, 15, 17, 22, 23, 24, 25,
27) is free.

## 2. MH-SD microSD SPI module (confirmed working end-to-end)

| MH-SD pin | Connects to | Pi physical pin |
|---|---|---|
| VCC / 3V3 | 3V3 | 1 |
| GND | GND | 6 |
| MOSI (DI) | GPIO10 | 19 |
| MISO (DO) | GPIO9 | 21 |
| SCK (CLK) | GPIO11 | 23 |
| CS | GPIO8 | 24 |

## 3. 10-segment LED bar graph — common anode, active-low (confirmed working)

This part is a 20-pin package, 2 rows of 10, where each pin pair (1↔20,
2↔19, 3↔18, ... 10↔11) is one LED — **pins 1-10 are the anodes, pins
11-20 are the cathodes.** This is wired **common anode**: all 10 anodes
tie together to **+3.3V**, each through its own 220Ω resistor. The
cathode side connects straight to the 10 GPIO pins below with **no
resistor on that side** — the resistors already did their job on the
anode/power side. Because the GPIO is on the cathode side, each LED
lights when its GPIO is driven **LOW**, not high — `pi_status_box.py`
and `test_hardware.py` both pass `active_high=False` to `LEDBarGraph` to
account for this.

The physical row labeling on these parts is inconsistent between units
("the label is random," per the kit this part is likely from) — on the
actual hardware here, the part needed to be **physically rotated 180°**
from its first mounting before it lit up correctly. If a fresh unit
doesn't light correctly, try that before assuming the wiring or code is
wrong.

| Segment | Anode (pin 1-10) → | Cathode (pin 11-20) → GPIO | Physical pin |
|---|---|---|---|
| 1 | 220Ω → +3.3V | GPIO5  | 29 |
| 2 | 220Ω → +3.3V | GPIO6  | 31 |
| 3 | 220Ω → +3.3V | GPIO12 | 32 |
| 4 | 220Ω → +3.3V | GPIO13 | 33 |
| 5 | 220Ω → +3.3V | GPIO19 | 35 |
| 6 | 220Ω → +3.3V | GPIO16 | 36 |
| 7 | 220Ω → +3.3V | GPIO26 | 37 |
| 8 | 220Ω → +3.3V | GPIO20 | 38 |
| 9 | 220Ω → +3.3V | GPIO21 | 40 |
| 10 | 220Ω → +3.3V | GPIO18 | 12 |

Meaning in software: normally fills 0-10 segments as power → internet →
Azure checks pass. While `cli.py` is processing a card (which the udev
rule triggers automatically the moment a card is inserted), it instead
runs a back-and-forth chase animation, then flashes solid (success) or
blinks (failure) before returning to the readiness display.

## 4. Safe-shutdown button — dropped

Removed from the project. Two different physical buttons were tried
across every leg-pair combination and neither gave a clean press signal
(GPIO4 read constantly `PRESSED` the moment any two legs were connected,
regardless of actual press state, and reverted to `not pressed` only when
fully unplugged) — confirmed via a bypass test that the Pi/GPIO4/pull-up
and the code were all fine, so the fault was isolated to that breadboard
section or the parts themselves. Not worth the remaining project time to
keep chasing. To shut the Pi down safely, SSH in and run:

```bash
sudo shutdown -h now
```

---

## Status as of this writing

- MH-SD SPI reader: wired and confirmed working end-to-end (`mmcblk3` /
  `mmcblk3p1` show up when a card is inserted).
- Bar graph: wired and confirmed working end-to-end — fills 0→10, runs
  the job-busy chase animation, and flashes correctly.
- Matrix + 2x74HC595: dropped from the project entirely.
- Button: dropped from the project entirely (see section 4).
- Code (`pi_status_box.py`, `test_hardware.py`, `deploy/`): updated to
  match — all button/matrix code, the button's sudoers rule, and related
  docs removed.
