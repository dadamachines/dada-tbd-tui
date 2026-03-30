# Error Codes

Reference for all `[Exxx]` error codes shown by the flash tool.
Quote the code when reporting issues.

---

## E1xx — Network & Firmware Catalog

| Code | Message | Cause | Fix |
|------|---------|-------|-----|
| E101 | Download failed: HTTP {code} | CDN returned an HTTP error (404, 500, etc.) | Check your internet connection. If 404, the firmware version may have been removed — try a different version. |
| E102 | Download failed: {reason} | DNS or connection error | Check internet connection and firewall. The CDN is `dadamachines.github.io`. |
| E103 | Download failed: {error} | File I/O error while saving the download | Check disk space and write permissions in your temp directory. |
| E104 | Could not fetch releases | `releases.json` unreachable after 3 retries | CDN may be down. Retry later, or check if the channel name is correct. |
| E105 | No versions found in catalog | `releases.json` exists but contains no versions | The channel may be empty. Switch to Stable channel. |
| E106 | No SD card image available | The selected firmware version has no SD card image | Choose a different version, or use Quick Update instead. |
| E107 | Cached file corrupted | SHA-256 of cached file doesn't match the hash recorded at download time | The file will be re-downloaded automatically. If this keeps happening, check your disk for errors. |

## E2xx — Environment & Prerequisites

| Code | Message | Cause | Fix |
|------|---------|-------|-----|
| E201 | Python 3.8+ required | Running on Python < 3.8 | Install Python 3.8 or newer. The launcher scripts (`flash.sh`, `flash.bat`) will guide you. |
| E202 | Python venv module not installed | `venv` or `ensurepip` not available | **Debian/Ubuntu:** `sudo apt install python3-venv` **Fedora/RHEL:** `sudo dnf install python3-libs` |
| E203 | Could not create venv | Virtual environment creation failed | Check disk space and permissions. Delete `.venv/` and retry. |
| E204 | pip install failed | `pip install esptool` returned an error | Check the pip output above the error. May be a network or permissions issue. |
| E205 | Installation timed out | pip took longer than 120 seconds | Retry — may be a slow network. |
| E206 | esptool install error | Unexpected error during esptool setup | Check the error details. Delete `.venv/` and retry. |
| E207 | No serial devices found | No USB serial ports detected | Connect the **front JTAG port** (USB-C #3). If the port still doesn't appear, hold the **BOOT button** while plugging in. |
| E208 | Invalid selection | Port selection didn't match any available port | Enter a valid port number or full path (e.g., `/dev/cu.usbmodem*`, `COM3`). |

## E3xx — Flashing (ESP32-P4, RP2350, MSC)

| Code | Message | Cause | Fix |
|------|---------|-------|-----|
| E301 | MSC firmware not found | The cached MSC firmware file is missing | Re-run the tool — it will re-download. |
| E302 | MSC firmware exceeds ota_1 | MSC binary is larger than the ota_1 partition | This is a firmware packaging issue. Report it to dadamachines. |
| E303 | MSC firmware flash failed | esptool write_flash returned a non-zero exit code | Check the esptool output above. Unplug and replug the JTAG cable, then retry. |
| E304 | MSC flash error | esptool could not be launched or crashed | Power-cycle the device and retry. Try a different USB port. |
| E305 | Firmware file not found | The cached P4 firmware file is missing | Re-run the tool — it will re-download. |
| E306 | Flashing failed | esptool write_flash returned a non-zero exit code | Check esptool output above. Power-cycle the TBD-16 and retry. Hold BOOT button while reconnecting if device is unresponsive. |
| E307 | Flash error | esptool could not be launched or crashed | Check USB connection. Try a different port or cable. |
| E308 | UF2 volume not found | RP2350 BOOTSEL volume didn't appear within 120 seconds | Hold **BOOTSEL** button, plug **back Port #2**, then release. Make sure the USB cable supports data (not charge-only). |
| E309 | Failed to copy UF2 | File copy to BOOTSEL volume failed | Retry the BOOTSEL sequence. Try a different USB port. |
| E310 | Recovery flash failed | P4 firmware flash during MSC recovery didn't succeed | Use menu option **[3] Flash ESP32-P4 only** to recover manually. |

## E4xx — SD Card Operations

| Code | Message | Cause | Fix |
|------|---------|-------|-----|
| E401 | SD card not found within timeout | SD card didn't mount within 90 seconds | Check that **back Port #1** is connected to your computer. Unplug and replug, wait 30 seconds. Try a powered USB hub. |
| E402 | REFUSED to erase | Safety validation blocked the erase (protected path, non-removable, etc.) | The selected path is not a valid removable SD card. Check the mount path. |
| E403 | Error erasing SD card | OS error while deleting files | Check that the SD card is not write-protected. Try reinserting the card. |
| E404 | Zip integrity check failed | The SD card image zip is corrupted | Delete the cached file (path shown in output) and re-download. |
| E405 | Not a valid zip | Downloaded file is not a zip archive | Cached file may be corrupted or an HTML error page. Delete it and re-download. |
| E406 | Extraction failed | OS error during zip extraction | Check disk space on the SD card. Reformat the SD card (FAT32) and retry. |
| E407 | Directory not found | The SD card mount path entered by the user doesn't exist | Check the path. On macOS: `/Volumes/NO NAME`. On Linux: `/media/<user>/<name>`. |

## E5xx — Menu & Input

| Code | Message | Cause | Fix |
|------|---------|-------|-----|
| E501 | Invalid choice | Menu selection not recognized | Enter one of the numbers shown in the menu. |
