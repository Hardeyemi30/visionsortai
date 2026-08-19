# Raspberry Pi 4B Status Box — Wiring Guide (1-chip, superseded)

> **A second SN74HC595 is now available — see `pi_status_box_wiring_2chip.md`
> for the current wiring.** That version chains both chips together to
> drive the matrix's rows *and* columns, using only 3 GPIO pins for the
> whole matrix instead of 11, and freeing 8 pins (including the serial
> console and I2C pins) that this 1-chip version used up. This file is
> kept for reference only — don't rewire to match it.

Four components, one 40-pin header. **This version uses only one SN74HC595** (a second one wasn't available), which means the matrix now needs 8 direct GPIO pins in addition to the shift register — and that uses up every single usable GPIO pin on the header. See the callout after the pin table for what that costs you. This covers physical wiring only — see `pi_status_box.py` for the control software.

## Parts

| # | Part | What it does |
|---|---|---|
| 1 | MH-SD Card Module (SPI full-size SD breakout) | Storage — mounted read/write over SPI |
| 2 | 2510SR-1 — 10-segment LED bar graph | Readiness indicator: power / internet / Azure |
| 3 | 788BS — 8x8 LED dot matrix + 1x 74HC595 shift register | Loading / busy / done animation |
| 4 | Large arcade-style push button | "Safe to unplug" shutdown trigger |

## Extra parts you'll need that aren't in the kit photos

- **10x 220Ω resistors** for the bar graph (one per LED)
- **8x 220Ω resistors** for the matrix's row lines
- **A full-size SD card** to put in the MH-SD module for testing (not microSD — the slot on this board is full-size)
- Breadboard + jumper wires (you already have these — note four components on one board will use a lot of both, and this version needs 8 more individual wires for the matrix columns than the two-chip design would have; a second breadboard may help)
- One of the 0.1µF ("104") decoupling capacitors that came with the kit, across VCC/GND on the single 74HC595 (the second one is spare for now)

## GPIO pin budget (26 of 26 usable GPIO used — every pin is spoken for)

| Function | BCM GPIO | Physical pin |
|---|---|---|
| **MH-SD card — CS** | GPIO8 (SPI0 CE0) | 24 |
| **MH-SD card — MOSI** | GPIO10 | 19 |
| **MH-SD card — MISO** | GPIO9 | 21 |
| **MH-SD card — SCK** | GPIO11 | 23 |
| **MH-SD card — 3V3** | — | 1 |
| **MH-SD card — GND** | — | 6 |
| **Matrix shift reg (rows) — DATA (DS)** | GPIO17 | 11 |
| **Matrix shift reg (rows) — LATCH (ST_CP)** | GPIO27 | 13 |
| **Matrix shift reg (rows) — CLOCK (SH_CP)** | GPIO22 | 15 |
| **Matrix shift reg — VCC** | — | 17 (3V3) |
| **Matrix shift reg — GND** | — | 14 |
| **Matrix column 1 (direct)** | GPIO2 | 3 |
| **Matrix column 2 (direct)** | GPIO3 | 5 |
| **Matrix column 3 (direct)** | GPIO7 | 26 |
| **Matrix column 4 (direct)** | GPIO14 | 8 |
| **Matrix column 5 (direct)** | GPIO15 | 10 |
| **Matrix column 6 (direct)** | GPIO23 | 16 |
| **Matrix column 7 (direct)** | GPIO24 | 18 |
| **Matrix column 8 (direct)** | GPIO25 | 22 |
| **Push button — signal** | GPIO4 | 7 |
| **Push button — GND** | — | 9 |
| **Bar graph LED 1** | GPIO5 | 29 |
| **Bar graph LED 2** | GPIO6 | 31 |
| **Bar graph LED 3** | GPIO12 | 32 |
| **Bar graph LED 4** | GPIO13 | 33 |
| **Bar graph LED 5** | GPIO16 | 36 |
| **Bar graph LED 6** | GPIO19 | 35 |
| **Bar graph LED 7** | GPIO20 | 38 |
| **Bar graph LED 8** | GPIO21 | 40 |
| **Bar graph LED 9** | GPIO26 | 37 |
| **Bar graph LED 10** | GPIO18 | 12 |
| **Bar graph — common rail** | — | any GND pin |

**Nothing is left free.** This design uses all 26 usable GPIO pins, which means:

- **No serial console.** GPIO14/GPIO15 are normally the UART TX/RX pins used to debug a Pi over a serial cable — they're now matrix column wires instead.
- **No I2C.** GPIO2/GPIO3 are the standard I2C pins — also repurposed as matrix columns, so no I2C sensors or displays can be added without freeing pins.
- **No room for the RFID module** (or anything else) unless something already wired here gets removed or moved to a different interface later.

If any of that turns out to matter, tracking down a second SN74HC595 later (even a single spare chip from a different kit or a cheap online order) would let you drop straight back to the 3-pin matrix design and get 8 pins back — nothing about this wiring is permanent.

**Important — use the Pi's 3V3 rail, not 5V, for all of these.** Pi GPIO pins are only 3.3V-tolerant. The MH-SD module has an onboard regulator that would let you use 5V, but there's no reason to — 3V3 is simpler and matches Pi logic natively.

## 1. MH-SD Card Module (full-size SD)

| Module pin | Connects to |
|---|---|
| 3V3 | Pi Pin 1 |
| GND | Pi Pin 6 |
| MOSI | Pi Pin 19 (GPIO10) |
| MISO | Pi Pin 21 (GPIO9) |
| SCK | Pi Pin 23 (GPIO11) |
| CS | Pi Pin 24 (GPIO8) |

Leave the module's 5V pin and second GND pin unconnected.

This board only becomes a mountable drive once SPI is enabled and the `mmc_spi` kernel driver is bound to it via a device tree overlay — that's a software step, covered separately (flagged as a follow-up item — see the note at the end of this guide).

## 2. LED Bar Graph (2510SR-1)

Each of the 10 LEDs gets its own 220Ω resistor in series before it reaches its GPIO pin. Tie all 10 LED cathodes (the common return leg) together to a single GND pin on the breadboard's ground rail.

```
GPIO pin --- 220Ω resistor --- LED anode      LED cathode --- GND rail
```

Repeat for all 10 segments, using the 10 GPIO pins listed in the table above. If a segment doesn't light, the two legs of that LED are probably swapped — flip it, LEDs aren't damaged by this.

## 3. 8x8 LED Matrix (788BS) via 1x 74HC595 + 8 direct GPIO

With only one shift register, it handles the 8 row lines (through the 8x 220Ω resistors — only one row is ever lit at a time during the refresh scan, so one resistor per row is enough current limiting), and the 8 column lines are wired straight to 8 individual Pi GPIO pins instead of a second chip (no resistors needed there, since at most one LED per column conducts at any instant).

- The 74HC595's 8 outputs (QA–QH) → the matrix's 8 row pins, each through a 220Ω resistor.
- The matrix's 8 column pins → directly to GPIO2, GPIO3, GPIO7, GPIO14, GPIO15, GPIO23, GPIO24, GPIO25 (physical pins 3, 5, 26, 8, 10, 16, 18, 22) — see the pin table above for which column goes where.
- The 74HC595: VCC to Pi 3V3 (pin 17), GND to Pi GND (pin 14), tie `MR`/reset pin (pin 10) to VCC and `OE`/output-enable pin (pin 13) to GND so outputs are always active. Put the 0.1µF decoupling cap across its VCC/GND pins.
- DATA, LATCH, and CLOCK from the Pi go to the shift register only (GPIO17 / GPIO27 / GPIO22) — the column pins bypass it entirely.

The exact row/column pin numbering on your specific 788BS depends on its datasheet orientation, and now the exact GPIO-to-column mapping matters more since there's no shift register to reorder bits in code. The software lets you flip rows/columns/mirror if the picture displayed comes out flipped or scrambled, so don't worry about getting the physical orientation perfect on the first try — just note down which physical column each wire actually landed on so we can match it in the code if needed.

## 4. Push Button

One leg of the button to GPIO4 (Pin 7), the other leg to GND (Pin 9). No resistor needed — the software uses the Pi's internal pull-up resistor, so the pin reads HIGH normally and LOW when pressed.

---

**Open item:** actually mounting the MH-SD card as usable storage on a Pi 4 needs a custom-compiled `mmc_spi` device tree overlay (Raspberry Pi OS doesn't ship one). That's a separate software step from this wiring guide — let me know when you're ready to tackle it and we'll build the overlay together.
