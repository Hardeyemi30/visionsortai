# Pi Status Box — Full Wiring Reference (2-chip design, superseded)

> **The matrix has been dropped from this project entirely — see
> `pi_status_box_wiring_final.md` for the current wiring.** Neither
> 74HC595 chip is needed anymore; the bar graph now covers the
> job-in-progress indicator too. This file is kept for reference only.

Now that you have a **second SN74HC595 shift register**, the 8x8 matrix's
rows *and* columns can both be driven from shift registers, chained
together. That frees the 8 GPIO pins that were previously wired directly
to the matrix columns — including **GPIO7**, which is the pin that was
colliding with the SPI0 CE1 (`spidev1`) kernel driver and causing the
`lgpio.error: 'GPIO busy'` crash.

**Net effect: GPIO usage drops from 26/26 pins to 18/26**, restoring the
serial console (GPIO14/15), I2C (GPIO2/3), and 8 pins of headroom for
anything else you want to add later.

> Software note: this wiring change requires an update to `Matrix8x8` in
> `pi_status_box.py` (shift 16 bits — columns byte then rows byte — instead
> of 8 bits + 8 direct-GPIO columns). Wire everything first, then say the
> word and I'll update the code to match before you test it.

---

## 1. GPIO pin budget (18 of 26 used)

| GPIO (BCM) | Physical pin | Assigned to |
|---|---|---|
| GPIO2  | 3  | *free* (was matrix column — also I2C SDA) |
| GPIO3  | 5  | *free* (was matrix column — also I2C SCL) |
| GPIO4  | 7  | **Button** — safe-shutdown |
| GPIO14 | 8  | *free* (was matrix column — also UART TXD) |
| GPIO15 | 10 | *free* (was matrix column — also UART RXD) |
| GPIO17 | 11 | **Matrix** — DATA (into U1 pin 14 / DS) |
| GPIO18 | 12 | **Bar graph** — segment 10 |
| GPIO27 | 13 | **Matrix** — LATCH (U1 + U2 pin 12 / ST_CP, shared) |
| GPIO22 | 15 | **Matrix** — CLOCK (U1 + U2 pin 11 / SH_CP, shared) |
| GPIO23 | 16 | *free* (was matrix column) |
| GPIO24 | 18 | *free* (was matrix column) |
| GPIO10 | 19 | **MH-SD** — MOSI (hardware SPI0) |
| GPIO9  | 21 | **MH-SD** — MISO (hardware SPI0) |
| GPIO25 | 22 | *free* (was matrix column) |
| GPIO11 | 23 | **MH-SD** — SCLK (hardware SPI0) |
| GPIO8  | 24 | **MH-SD** — CE0 (hardware SPI0) |
| GPIO7  | 26 | *free* (was matrix column — also SPI0 CE1) |
| GPIO5  | 29 | **Bar graph** — segment 1 |
| GPIO6  | 31 | **Bar graph** — segment 2 |
| GPIO12 | 32 | **Bar graph** — segment 3 |
| GPIO13 | 33 | **Bar graph** — segment 4 |
| GPIO19 | 35 | **Bar graph** — segment 5 |
| GPIO16 | 36 | **Bar graph** — segment 6 |
| GPIO26 | 37 | **Bar graph** — segment 7 |
| GPIO20 | 38 | **Bar graph** — segment 8 |
| GPIO21 | 40 | **Bar graph** — segment 9 |

Power/ground pins used: 3V3 (physical pin 1 and 17), 5V (pin 2 or 4, only
if you power the matrix LEDs from 5V instead — see matrix section), GND
(pins 6, 9, 14, 20, 25, 30, 34, 39 — use whichever are convenient).

---

## 2. 74HC595 shift register pinout (applies to BOTH chips)

Your two chips are different manufacturers (NXP `74HCT595N` and TI
`SN74HC595N`) but they are pin-for-pin compatible — wire them identically.

```
                 ┌────────∪────────┐
        Q1  ───►│ 1             16 │◄─── VCC (3V3)
        Q2  ───►│ 2             15 │◄─── Q0
        Q3  ───►│ 3             14 │◄─── DS (serial data in)
        Q4  ───►│ 4             13 │◄─── OE (active-low, tie to GND)
        Q5  ───►│ 5             12 │◄─── ST_CP / RCLK (latch)
        Q6  ───►│ 6             11 │◄─── SH_CP / SRCLK (shift clock)
        Q7  ───►│ 7             10 │◄─── MR / SRCLR (active-low reset, tie to 3V3)
       GND  ───►│ 8              9 │───► Q7' / QH' (serial data out → next chip's DS)
                 └──────────────────┘
```

Notch/dot at pin 1 end (top-left in the diagram above) — same orientation
convention you already used for the single-chip build.

Always tie on **every** 74HC595 in the design:
- Pin 10 (MR) → 3V3 (keeps the register out of reset)
- Pin 13 (OE) → GND (keeps outputs always enabled)
- Pin 8 (GND) → GND rail
- Pin 16 (VCC) → 3V3 rail

---

## 3. MH-SD microSD SPI module (unchanged)

| MH-SD pin | Connects to | Pi physical pin |
|---|---|---|
| VCC / 3V3 | 3V3 | 1 |
| GND | GND | 6 |
| MOSI (DI) | GPIO10 | 19 |
| MISO (DO) | GPIO9 | 21 |
| SCK (CLK) | GPIO11 | 23 |
| CS | GPIO8 | 24 |

This is the SPI0 hardware interface — no shift register involved. Already
wired and confirmed working (card mounts as `/dev/mmcblkN`).

---

## 4. 10-segment LED bar graph (unchanged — still direct GPIO)

Both chips are committed to the matrix (see below), so the bar graph
stays on 10 direct GPIO pins, each through a **220Ω resistor** in series
with the segment's anode, common cathode to GND (or reverse if your bar
graph is common-anode — check the datasheet/continuity-test it if unsure,
same as before).

| Segment | GPIO | Physical pin |
|---|---|---|
| 1 | GPIO5  | 29 |
| 2 | GPIO6  | 31 |
| 3 | GPIO12 | 32 |
| 4 | GPIO13 | 33 |
| 5 | GPIO19 | 35 |
| 6 | GPIO16 | 36 |
| 7 | GPIO26 | 37 |
| 8 | GPIO20 | 38 |
| 9 | GPIO21 | 40 |
| 10 | GPIO18 | 12 |

Meaning in software (`ReadinessMonitor`): segments light up progressively
as power → internet → Azure checks pass.

---

## 5. 8x8 LED matrix — NEW 2-chip daisy chain

**U1 drives the 8 ROWS. U2 drives the 8 COLUMNS.** U1's serial-out (pin 9)
feeds directly into U2's serial-in (pin 14) — that's the "daisy chain."
CLOCK and LATCH are wired in **parallel** to both chips (same two Pi pins
go to both chips' pin 11 and pin 12). DATA from the Pi only goes to **U1**.

```
                 ┌── GPIO17 (pin 11, Pi)
                 │
                 ▼
   Pi ──DATA──► U1.DS(14)      U1.Q7'(9) ──────► U2.DS(14)
   Pi ──CLOCK─► U1.SH_CP(11) ──┬─────────────────► U2.SH_CP(11)
   Pi ──LATCH─► U1.ST_CP(12) ──┴─────────────────► U2.ST_CP(12)

   U1 outputs Q0-Q7 (pins 15,1,2,3,4,5,6,7) ──► Matrix ROW 1-8 (direct, no resistor)
   U2 outputs Q0-Q7 (pins 15,1,2,3,4,5,6,7) ──► 220Ω resistor ──► Matrix COL 1-8
```

### Pi → U1 connections (start of chain)

| Signal | U1 pin | Pi GPIO | Physical pin |
|---|---|---|---|
| DATA (DS) | 14 | GPIO17 | 11 |
| CLOCK (SH_CP) | 11 | GPIO22 | 15 |
| LATCH (ST_CP) | 12 | GPIO27 | 13 |
| MR | 10 | → 3V3 | 1 or 17 |
| OE | 13 | → GND | any GND |
| GND | 8 | → GND | any GND |
| VCC | 16 | → 3V3 | 1 or 17 |

### U1 → U2 daisy chain + shared clock/latch

| Signal | U1 pin | U2 pin |
|---|---|---|
| Serial chain | Q7' (pin 9) | DS (pin 14) |
| Shared clock | SH_CP (pin 11) | SH_CP (pin 11) |
| Shared latch | ST_CP (pin 12) | ST_CP (pin 12) |

U2 also needs its own MR→3V3, OE→GND, GND→GND, VCC→3V3 (pins 10, 13, 8,
16 respectively) — same as U1. U2's pin 9 (Q7') is unused (end of chain).

### U1 outputs → matrix rows

| U1 pin | Output | Matrix row |
|---|---|---|
| 15 | Q0 | ROW 1 |
| 1  | Q1 | ROW 2 |
| 2  | Q2 | ROW 3 |
| 3  | Q3 | ROW 4 |
| 4  | Q4 | ROW 5 |
| 5  | Q5 | ROW 6 |
| 6  | Q6 | ROW 7 |
| 7  | Q7 | ROW 8 |

### U2 outputs → matrix columns (each through a 220Ω resistor)

| U2 pin | Output | Matrix column |
|---|---|---|
| 15 | Q0 | COL 1 |
| 1  | Q1 | COL 2 |
| 2  | Q2 | COL 3 |
| 3  | Q3 | COL 4 |
| 4  | Q4 | COL 5 |
| 5  | Q5 | COL 6 |
| 6  | Q6 | COL 7 |
| 7  | Q7 | COL 8 |

Matrix row/column pin numbers depend on your specific 788BS part and
aren't standardized across manufacturers — normally you'd need to
continuity-test each of the 16 header pins to be sure. In this case,
though, the GPIO pins already chosen for this project (17/27/22 for
data/latch/clock) turned out to exactly match the Freenove RFID Starter
Kit's own official "74HC595 & LED Matrix" tutorial, which is a strong
signal this project's matrix and chips came from that kit. Their
tutorial gives this physical pinout for their common-anode 8x8 matrix
(same physical numbering convention as section 2 above — top edge
left-to-right = 16,15,14,13,12,11,10,9; bottom edge left-to-right =
1,2,3,4,5,6,7,8):

| Logical | Physical pin | | Logical | Physical pin |
|---|---|---|---|---|
| ROW 1 | 9  | | COL 1 | 13 |
| ROW 2 | 14 | | COL 2 | 3  |
| ROW 3 | 8  | | COL 3 | 4  |
| ROW 4 | 12 | | COL 4 | 10 |
| ROW 5 | 1  | | COL 5 | 6  |
| ROW 6 | 7  | | COL 6 | 11 |
| ROW 7 | 2  | | COL 7 | 15 |
| ROW 8 | 5  | | COL 8 | 16 |

Worth a quick continuity-test spot-check on 1-2 pins before committing to
the full wiring, since even kit-sourced parts can vary between
production batches — but this table is the strong default. Their kit
also confirmed the matrix is common-anode, matching the polarity this
project's code already assumes (rows driven HIGH to select, columns
sunk LOW to light individual LEDs).

Note: Freenove's own reference circuit assigns chip roles the other way
round from this doc (their chip closest to the Pi drives columns, the
chained one drives rows) — electrically equivalent, just a different
labeling choice. This project deliberately kept U1=rows/U2=columns as
already built, so wire it per the U1/U2 tables above, not per Freenove's
schematic, if you're cross-referencing their diagram.

---

## 6. Safe-shutdown button (unchanged)

| Button pin | Connects to |
|---|---|
| One leg | GPIO4 (physical pin 7) |
| Other leg | GND (any GND pin) |

Uses the Pi's internal pull-up (`Button(4)` in gpiozero defaults to
`pull_up=True`) — no external resistor needed. Press = pulls GPIO4 low.

---

## 7. What changed vs. the 1-chip build

- Removed: 8 direct GPIO wires from the Pi to the matrix columns
  (GPIO2, 3, 7, 14, 15, 23, 24, 25).
- Added: U2 (second 74HC595), wired to U1's serial-out and the shared
  clock/latch lines, driving the matrix columns instead.
- GPIO7 is now completely unused by this project. The `spidev1`-disable
  fragment we added to the device tree overlay earlier is no longer
  strictly necessary, but it's harmless to leave in place — nothing else
  needs SPI0 CE1 either.
- Once wiring is done, `pi_status_box.py`'s `Matrix8x8` class needs a
  rewrite: shift out 16 bits per frame (columns byte first, then rows
  byte, so the rows byte ends up latched in U1) instead of 8 bits +
  toggling 8 direct `OutputDevice`s. Let me know when you're ready and
  I'll make that change.
