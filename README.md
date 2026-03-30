# dadamachines TBD-16 Firmware Update Tool

A terminal-based wizard to update your **dadamachines TBD-16** — flash ESP32-P4 firmware, RP2350 (Pico) firmware, and deploy SD card images. An alternative to the [browser-based flash tool](https://dadamachines.github.io/ctag-tbd/flash/10_stable_channel.html).

Works on **macOS**, **Linux**, and **Windows**.

## Installation

### Download

```bash
git clone https://github.com/dadamachines/dada-tbd-tui.git
cd dada-tbd-tui
```

Or download and unzip from [GitHub Releases](https://github.com/dadamachines/dada-tbd-tui/releases).

### Run

**macOS / Linux:**
```bash
./flash.sh
```

**Windows:**
```
flash.bat
```

That's it. The launcher scripts find (or help you install) Python automatically, and `esptool` is installed into a local virtual environment on first run — no manual setup needed.

> **Already have Python 3.8+?** You can also run `python3 flash_tool.py` directly.

## Quick Start

```bash
# Interactive wizard (recommended)
./flash.sh

# Quick update — flash latest stable P4 + Pico firmware
./flash.sh --quick

# Full SD card deploy — erase & re-write SD + flash firmware
./flash.sh --full

# Use staging/beta channel
./flash.sh --quick --channel staging
```

On Windows, replace `./flash.sh` with `flash.bat`.

## Update Methods

### ⚡ Quick Update
Flash P4 + Pico firmware. Keeps your SD card data (samples, presets, macros) intact.

1. Connect **front JTAG port** (USB-C #3) + a **back port** (#1 or #2) for power
2. Tool downloads & flashes the ESP32-P4 firmware
3. Connect **back Port #2**, put RP2350 in BOOTSEL mode → tool copies UF2

### 🗄️ Full SD Card Deploy
Erase & re-write the SD card image, then flash all firmware. Use for fresh installs or SD card recovery.

**Via USB (MSC mode)** — no need to open the device:
1. Tool flashes MSC firmware → SD card appears as USB drive on back Port #1
2. Downloads & extracts the SD card image
3. Flashes the P4 firmware (restores normal boot)
4. Flashes the RP2350 Pico firmware

**Via external card reader** — requires opening the device:
1. Remove SD card, insert into reader
2. Tool writes the SD card image
3. Re-insert card, flash firmware as above

## Hardware Ports

| Port | Location | Purpose |
|------|----------|---------|
| USB-C #3 (JTAG) | **Front** | Serial flash (P4) |
| USB-C #1 | **Back** | Power + USB Ethernet (WebUI) + USB MIDI + SD card (MSC) |
| USB-C #2 | **Back** (edge) | Power + RP2350 BOOTSEL flash |

## CLI Options

```
--quick              Quick Update (P4 + Pico, no SD erase)
--full               Full SD Deploy (SD image + P4 + Pico)
--channel {stable,staging}   Firmware channel (default: stable)
--p4-only            Flash only ESP32-P4
--pico-only          Flash only RP2350 Pico
--install-esptool    Install/upgrade esptool
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Python not found` | Install Python 3.8+ — the launcher scripts will guide you |
| `ensurepip is not available` (Linux) | `sudo apt install python3-venv` (Debian/Ubuntu) or `sudo dnf install python3-libs` (Fedora) |
| No serial port detected | Make sure the **front JTAG USB-C port** is connected and try "Scan again" |
| `No serial data received` | Try the other USB port, or a different cable |
| Flash fails / timeout | Re-run — the tool retries automatically. Power-cycle the TBD-16 if stuck |
| SD card not appearing | Wait 20–30 seconds after MSC mode flash. Check back USB-C port #1 is connected |
| Device crashes after SD update | macOS `._` dot-files — the tool cleans these automatically. Re-run Full SD Deploy if needed |

## Firmware Source

All firmware is served from [dadamachines.github.io/dada-tbd-firmware](https://dadamachines.github.io/dada-tbd-firmware/) (GitHub Pages CDN). Downloads are cached in your system temp directory.