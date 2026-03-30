#!/usr/bin/env python3
"""
dadamachines TBD-16 Firmware Update Tool
─────────────────────────────────────────
Terminal wizard to flash ESP32-P4 and RP2350 firmware,
and optionally re-deploy the SD card image on your TBD-16.

Usage:
    python3 flash_tool.py                    # Interactive wizard
    python3 flash_tool.py --quick            # Quick update (latest stable)
    python3 flash_tool.py --full             # Full SD card deploy (latest stable)
    python3 flash_tool.py --channel staging  # Use beta channel
    python3 flash_tool.py --channel beta     # Same as staging
    python3 flash_tool.py --help             # Show all options
"""

import os
import sys
import subprocess
import platform
import glob
import time
import json
import shutil
import zipfile
import tempfile
import argparse
import struct
import zlib
from pathlib import Path

# ═══════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════
FIRMWARE_CDN = "https://dadamachines.github.io/dada-tbd-firmware"
MSC_FW_PATH = "utilities/dada-tbd-16-tusb_msc-p4/dada-tbd-16-tusb-msc.bin"

CHIP = "esp32p4"
BAUD = "921600"
UNIFIED_OFFSET = "0x0"

# Partition table addresses
PT_ADDR = 0x8000           # partition table location in flash
PT_ENTRY_SIZE = 32         # bytes per partition entry
PT_MAGIC = b'\xAA\x50'    # magic bytes at start of each entry
PT_MD5_MAGIC = b'\xEB\xEB' # marks MD5 checksum (end of table)
OTA_DATA_ADDR = 0xd000     # OTA select data location
OTA_DATA_SIZE = 0x2000     # 8 KB (two 4 KB sectors)

# Fallback ota_1 address — used only if partition table can't be read.
# Matches partitions_example.csv: ota_0 at 0x10000 (5 MB), ota_1 at 0x510000 (1 MB).
FALLBACK_OTA1_ADDR = 0x510000

CACHE_DIR = os.path.join(tempfile.gettempdir(), "dada-tbd-firmware")
VENV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv")

SD_MOUNT_TIMEOUT = 90       # seconds to wait for SD card mount
UF2_MOUNT_TIMEOUT = 120     # seconds to wait for UF2 volume
POST_FLASH_DELAY = 5        # seconds after flash before next step

SD_VOLUME_NAMES = ["NO NAME"]
# macOS system metadata — skip during erase (they regenerate automatically)
MACOS_SYSTEM_DIRS = {".Spotlight-V100", ".fseventsd", ".Trashes", ".DS_Store",
                     ".TemporaryItems", ".VolumeIcon.icns"}
# Paths we must NEVER erase — hard safety block
PROTECTED_PATHS = {
    "/", "/System", "/Users", "/Applications", "/Library", "/Volumes/Macintosh HD",
    "/home", "/root", "/usr", "/var", "/etc", "/opt", "/bin", "/sbin",
    "C:\\", "C:\\Windows", "C:\\Users", "C:\\Program Files",
}


# ═══════════════════════════════════════════════════
#  ANSI STYLING
# ═══════════════════════════════════════════════════
class S:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"


# ═══════════════════════════════════════════════════
#  UI HELPERS
# ═══════════════════════════════════════════════════
def clear():
    os.system("cls" if platform.system() == "Windows" else "clear")


def status(msg, level="info"):
    icons = {
        "info": f"{S.BLUE}ℹ{S.RESET}",
        "ok":   f"{S.GREEN}✔{S.RESET}",
        "warn": f"{S.YELLOW}⚠{S.RESET}",
        "err":  f"{S.RED}✘{S.RESET}",
        "work": f"{S.CYAN}⟳{S.RESET}",
        "step": f"{S.MAGENTA}▶{S.RESET}",
    }
    print(f"  {icons.get(level, icons['info'])} {msg}")


def header(text):
    w = min(max(len(text) + 4, 50), 76)
    print(f"\n  {S.CYAN}{S.BOLD}{'─' * w}{S.RESET}")
    print(f"  {S.WHITE}{S.BOLD}  {text}{S.RESET}")
    print(f"  {S.CYAN}{S.BOLD}{'─' * w}{S.RESET}\n")


def ask(text, default=None):
    if default is not None:
        r = input(f"  {S.CYAN}▸{S.RESET} {text} [{S.CYAN}{S.BOLD}{default}{S.RESET}]: ").strip()
        return r if r else str(default)
    return input(f"  {S.CYAN}▸{S.RESET} {text}: ").strip()


def separator():
    print(f"  {S.DIM}{'─' * 55}{S.RESET}")


def banner():
    print(f"""{S.CYAN}{S.BOLD}
    ╔═════════════════════════════════════════════════════════╗
    ║                                                         ║
    ║   ⚡  dadamachines TBD-16  Firmware Update Tool  ⚡     ║
    ║                                                         ║
    ║   Flash ESP32-P4  •  Flash RP2350  •  Deploy SD Card    ║
    ║                                                         ║
    ╚═════════════════════════════════════════════════════════╝{S.RESET}
""")


def step_header(step, total, title):
    print()
    print(f"  {S.CYAN}{'─' * 55}{S.RESET}")
    print(f"  {S.CYAN}{S.BOLD}  Step {step}/{total}  │  {S.WHITE}{title}{S.RESET}")
    print(f"  {S.CYAN}{'─' * 55}{S.RESET}")
    print()


def action_box(lines):
    """Display a yellow-bordered box for user actions that require physical steps."""
    w = 55
    print()
    print(f"  {S.YELLOW}┌{'─' * w}┐{S.RESET}")
    print(f"  {S.YELLOW}│{S.RESET}  {S.YELLOW}{S.BOLD}👉 ACTION REQUIRED{S.RESET}{' ' * (w - 20)}{S.YELLOW}│{S.RESET}")
    print(f"  {S.YELLOW}│{' ' * w}│{S.RESET}")
    for line in lines:
        padding = w - 2 - len(line)
        if padding < 0:
            padding = 0
        print(f"  {S.YELLOW}│{S.RESET}  {S.WHITE}{line}{S.RESET}{' ' * padding}{S.YELLOW}│{S.RESET}")
    print(f"  {S.YELLOW}│{' ' * w}│{S.RESET}")
    print(f"  {S.YELLOW}└{'─' * w}┘{S.RESET}")
    print()


def _retry_prompt(step_name, tips=None):
    """Ask user to retry a failed step. Returns True to retry, False to abort."""
    print()
    if tips:
        status("Recovery suggestions:", "info")
        for tip in tips:
            status(f"  • {tip}", "info")
    print()
    return ask(f"Retry {step_name}? (y/n)", "y").lower() in ("y", "yes")


FLASH_RECOVERY_TIPS = [
    "Unplug and replug the JTAG cable (USB-C #3)",
    "Power cycle: unplug ALL cables → wait 3 seconds → reconnect",
    "Try a different USB port on your computer",
]

PORT_RECOVERY_TIPS = [
    "Unplug and replug the JTAG cable (USB-C #3)",
    "Make sure a back port is connected for power (Port #1 or #2)",
    "Try a different USB port on your computer",
]

PICO_RECOVERY_TIPS = [
    "Hold BOOTSEL button → plug back Port #2 → release button",
    "Make sure the USB cable supports data (not charge-only)",
    "Try a different USB port on your computer",
]


def completion_banner(title, firmware_tag=None):
    """Display a styled completion box matching the opening banner."""
    print(f"\n{S.CYAN}{S.BOLD}", end="")
    print("    ╔═════════════════════════════════════════════════════════╗")
    print("    ║                                                         ║")
    line = f"   ✓  {title}"
    print(f"    ║{line:<57}║")
    print("    ║                                                         ║")
    if firmware_tag:
        line2 = f"       Firmware: {firmware_tag}"
        print(f"    ║{line2:<57}║")
        print("    ║                                                         ║")
    print(f"    ╚═════════════════════════════════════════════════════════╝{S.RESET}")
    print()


def power_cycle_warning():
    """Display prominent power-cycle instructions. Call at end of every wizard."""
    print(f"\n{S.YELLOW}{S.BOLD}", end="")
    print("    ╔═════════════════════════════════════════════════════════╗")
    print("    ║                                                         ║")
    print("    ║   ⚠  Power-cycle your device to finish!                 ║")
    print("    ║                                                         ║")
    print("    ║   1. Disconnect ALL USB cables                          ║")
    print("    ║   2. Wait 5 seconds                                     ║")
    print("    ║   3. Reconnect ONLY back Port #1                        ║")
    print("    ║      (power + USB Ethernet + MIDI)                      ║")
    print("    ║                                                         ║")
    print(f"    ╚═════════════════════════════════════════════════════════╝{S.RESET}")
    print()
    print(f"    {S.CYAN}{S.BOLD}Then open the WebUI:{S.RESET}")
    print()
    url = "http://192.168.4.1"
    osc_url = f"\033]8;;{url}\033\\{S.CYAN}{S.BOLD}{url}{S.RESET}\033]8;;\033\\"
    print(f"      {osc_url}")
    print()
    if platform.system() == "Darwin":
        print(f"    {S.DIM}(Hold ⌘ and click the link, or copy it into your browser){S.RESET}")
    elif platform.system() == "Windows":
        print(f"    {S.DIM}(Hold Ctrl and click the link, or copy it into your browser){S.RESET}")
    else:
        print(f"    {S.DIM}(Ctrl+click the link, or copy it into your browser){S.RESET}")
    print()
    print(f"    {S.CYAN}{S.BOLD}Have fun with your dadamachines TBD-16! 🎶{S.RESET}")
    print()


def progress_bar(done, total, width=35):
    if total <= 0:
        return
    pct = min(done / total, 1.0)
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    mb_done = done / 1048576
    mb_total = total / 1048576
    sys.stdout.write(
        f"\r    [{S.CYAN}{bar}{S.RESET}] {pct * 100:5.1f}%  "
        f"({mb_done:.2f} / {mb_total:.2f} MB)")
    sys.stdout.flush()


# ═══════════════════════════════════════════════════
#  DOWNLOAD & CACHE
# ═══════════════════════════════════════════════════
def ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)
    return CACHE_DIR


def download_file(url, dest_path, label=None):
    """Download a file with progress bar. Returns True on success."""
    import urllib.request
    import urllib.error

    if label:
        status(f"Downloading {label} …", "work")
    status(f"URL → {S.DIM}{url}{S.RESET}", "info")

    try:
        def hook(blk, blk_sz, total):
            progress_bar(blk * blk_sz, total)

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        urllib.request.urlretrieve(url, dest_path, reporthook=hook)
        print()
        size_kb = os.path.getsize(dest_path) / 1024
        if size_kb > 1024:
            status(f"Downloaded → {os.path.basename(dest_path)} "
                   f"({size_kb / 1024:.1f} MB)", "ok")
        else:
            status(f"Downloaded → {os.path.basename(dest_path)} "
                   f"({size_kb:.1f} KB)", "ok")
        return True
    except urllib.error.HTTPError as e:
        print()
        status(f"Download failed: HTTP {e.code} — {e.reason}", "err")
    except urllib.error.URLError as e:
        print()
        status(f"Download failed: {e.reason}", "err")
    except Exception as e:
        print()
        status(f"Download failed: {e}", "err")
    return False


# ═══════════════════════════════════════════════════
#  FIRMWARE CDN — releases.json
# ═══════════════════════════════════════════════════

def _channel_label(channel):
    """Human-readable label for a channel name."""
    if channel == "stable":
        return "Stable Channel"
    if channel == "staging":
        return "Beta Channel (Staging)"
    if channel.startswith("feature-test-"):
        name = channel.replace("feature-test-", "")
        return f"Beta Channel (Feature: {name})"
    return channel.title()


def fetch_releases(channel="stable"):
    """Fetch releases.json from the firmware CDN. Retries up to 3 times."""
    import urllib.request
    import urllib.error

    url = f"{FIRMWARE_CDN}/{channel}/releases.json"
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt < 3:
                status(f"Could not fetch releases (attempt {attempt}/3): {e}", "warn")
                time.sleep(attempt * 2)
    status(f"Could not fetch releases for '{channel}' after 3 attempts", "err")
    return None


def select_channel():
    """Let user pick stable or beta. Returns channel name string."""
    print(f"      {S.GREEN}{S.BOLD}[1]{S.RESET}  Stable Channel   {S.DIM}(recommended){S.RESET}  {S.GREEN}◄{S.RESET}")
    print(f"      {S.YELLOW}{S.BOLD}[2]{S.RESET}  Beta Channel     {S.DIM}(pre-release / feature branches){S.RESET}")
    print()
    c = ask("Select channel", "1")
    if c != "2":
        return "stable"

    # Beta channel — discover staging + feature branches
    return _select_beta_channel()


def _discover_feature_branches():
    """Discover active feature-test branches from GitHub releases API."""
    import urllib.request
    import urllib.error

    api_url = "https://api.github.com/repos/dadamachines/ctag-tbd/releases?per_page=30"
    try:
        req = urllib.request.Request(api_url)
        req.add_header("Accept", "application/vnd.github+json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            releases = json.loads(resp.read().decode())
    except Exception:
        return []

    import re
    candidates = set()
    for r in releases:
        if not r.get("prerelease"):
            continue
        tag = r.get("tag_name", "")
        m = re.match(r'^(feature-test-[a-z0-9-]+)$', tag)
        if m and not re.search(r'-[0-9a-f]{7,}$', tag):
            candidates.add(m.group(1))

    # Verify each has a releases.json on CDN
    verified = []
    for name in sorted(candidates):
        try:
            url = f"{FIRMWARE_CDN}/{name}/releases.json"
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=5):
                verified.append(name)
        except Exception:
            pass

    return verified


def _select_beta_channel():
    """Show beta channel sub-menu: staging + feature branches."""
    status("Discovering beta channels …", "work")
    features = _discover_feature_branches()

    print()
    print(f"      {S.YELLOW}{S.BOLD}[1]{S.RESET}  Staging  {S.DIM}(latest pre-release){S.RESET}  {S.GREEN}◄{S.RESET}")

    for i, name in enumerate(features, 2):
        label = name.replace("feature-test-", "")
        print(f"      {S.YELLOW}{S.BOLD}[{i}]{S.RESET}  Feature: {label}")

    print()
    c = ask("Select beta channel", "1")

    try:
        idx = int(c) - 1
        if idx == 0:
            return "staging"
        if 0 < idx <= len(features):
            return features[idx - 1]
    except ValueError:
        pass

    status("Invalid selection, using staging", "warn")
    return "staging"


def select_version(catalog, channel="stable"):
    """Let user pick a firmware version from the catalog. Returns version dict.
    For non-stable channels, auto-selects the latest (only) version."""
    versions = catalog.get("versions", [])
    if not versions:
        status("No versions found in catalog!", "err")
        return None

    # For beta channels (staging / feature-test), always use latest — no picker
    if channel != "stable":
        v = versions[0]
        tag = v["tag"]
        ts = v.get("timestamp", "")[:10]
        status(f"Latest: {S.BOLD}{tag}{S.RESET}  {S.DIM}{ts}{S.RESET}", "ok")
        return v

    latest_tag = catalog.get("latest", "")

    print()
    for i, v in enumerate(versions[:10], 1):
        tag = v["tag"]
        ts = v.get("timestamp", "")[:10]
        is_latest = " ← latest" if tag == latest_tag else ""
        pico = "  + pico" if v.get("files", {}).get("pico") else ""
        marker = f"  {S.GREEN}◄{S.RESET}" if i == 1 else ""
        print(f"      {S.GREEN}{S.BOLD}[{i}]{S.RESET}  {tag}  "
              f"{S.DIM}{ts}{pico}{S.RESET}"
              f"{S.GREEN}{S.BOLD}{is_latest}{S.RESET}{marker}")

    if len(versions) > 10:
        print(f"      {S.DIM}… and {len(versions) - 10} more{S.RESET}")

    print()
    c = ask("Select version", "1")
    try:
        idx = int(c) - 1
        if 0 <= idx < len(versions):
            return versions[idx]
    except ValueError:
        pass

    status("Invalid selection, using latest", "warn")
    return versions[0]


def build_urls(version, channel="stable"):
    """Build download URLs for a selected version."""
    f = version.get("files", {})
    return {
        "tag": version["tag"],
        "p4_url": f"{FIRMWARE_CDN}/{f['unified']}" if f.get("unified") else None,
        "pico_url": f"{FIRMWARE_CDN}/{f['pico']}" if f.get("pico") else None,
        "sd_url": f"{FIRMWARE_CDN}/{f['sdcard']}" if f.get("sdcard") else None,
        "hash_url": f"{FIRMWARE_CDN}/{f['hash']}" if f.get("hash") else None,
        "msc_url": f"{FIRMWARE_CDN}/{MSC_FW_PATH}",
    }


# ═══════════════════════════════════════════════════
#  PREREQUISITES
# ═══════════════════════════════════════════════════
def check_python():
    v = sys.version_info
    if v.major >= 3 and v.minor >= 8:
        status(f"Python {v.major}.{v.minor}.{v.micro}", "ok")
        return True
    status(f"Python 3.8+ required (found {v.major}.{v.minor})", "err")
    return False


def _venv_python():
    """Return the Python executable path inside the local venv."""
    if platform.system() == "Windows":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python3")


def _try_esptool(python_cmd):
    """Try running esptool with given python. Returns version string or None."""
    try:
        r = subprocess.run(
            [python_cmd, "-m", "esptool", "version"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip().splitlines()[0]
    except (FileNotFoundError, Exception):
        pass
    return None


def find_esptool():
    """Find esptool. Checks local venv first, then system. Returns command list or None."""
    # 1. Check local venv
    venv_py = _venv_python()
    if os.path.isfile(venv_py):
        ver = _try_esptool(venv_py)
        if ver:
            status(f"esptool: {ver} (local venv)", "ok")
            return [venv_py, "-m", "esptool"]

    # 2. Check current Python
    ver = _try_esptool(sys.executable)
    if ver:
        status(f"esptool: {ver}", "ok")
        return [sys.executable, "-m", "esptool"]

    # 3. Check esptool.py in PATH
    try:
        r = subprocess.run(["esptool.py", "version"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            ver = r.stdout.strip().splitlines()[0]
            status(f"esptool: {ver} (system)", "ok")
            return ["esptool.py"]
    except (FileNotFoundError, Exception):
        pass

    return None


def install_esptool():
    """Install esptool into a local venv. Returns True on success."""
    # Create venv if it doesn't exist
    venv_py = _venv_python()
    if not os.path.isfile(venv_py):
        status("Creating local Python environment …", "work")
        try:
            import venv
            venv.create(VENV_DIR, with_pip=True)
            status("Virtual environment created", "ok")
        except Exception:
            # Fallback: use subprocess
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "venv", VENV_DIR],
                    capture_output=True, text=True, timeout=60)
                if r.returncode != 0:
                    err_msg = r.stderr.strip()
                    # Detect missing python3-venv on Debian/Ubuntu
                    if "ensurepip" in err_msg or "No module named" in err_msg:
                        status("Python venv module is not installed", "err")
                        if platform.system() == "Linux":
                            status("Fix with:", "info")
                            status(f"  sudo apt install python3-venv   {S.DIM}(Debian/Ubuntu){S.RESET}", "info")
                            status(f"  sudo dnf install python3-libs   {S.DIM}(Fedora/RHEL){S.RESET}", "info")
                        else:
                            status(f"Error: {err_msg}", "err")
                        return False
                    status(f"Could not create venv: {err_msg}", "err")
                    return False
                status("Virtual environment created", "ok")
            except Exception as e:
                status(f"Could not create venv: {e}", "err")
                return False

    status("Installing esptool …", "work")
    print()
    try:
        r = subprocess.run(
            [venv_py, "-m", "pip", "install", "--upgrade", "esptool"],
            timeout=120,
        )
        print()
        if r.returncode == 0:
            status("esptool installed!", "ok")
            return True
        status("pip install failed (see output above)", "err")
    except subprocess.TimeoutExpired:
        status("Installation timed out", "err")
    except Exception as e:
        status(f"Error: {e}", "err")
    return False


def ensure_esptool():
    """Make sure esptool is available. Install if needed. Returns cmd list or None."""
    cmd = find_esptool()
    if cmd:
        return cmd

    status("esptool not found — will install automatically", "info")

    if not install_esptool():
        return None

    return find_esptool()


# ═══════════════════════════════════════════════════
#  SERIAL PORT DETECTION
# ═══════════════════════════════════════════════════
def find_serial_ports():
    """Scan for serial ports."""
    ports = []
    osname = platform.system()
    if osname == "Linux":
        for p in ("/dev/ttyUSB*", "/dev/ttyACM*"):
            ports.extend(glob.glob(p))
    elif osname == "Darwin":
        for p in ("/dev/cu.usbmodem*", "/dev/cu.usbserial*",
                   "/dev/cu.SLAB*", "/dev/cu.wchusbserial*"):
            ports.extend(glob.glob(p))
    elif osname == "Windows":
        try:
            import serial.tools.list_ports
            ports = [p.device for p in serial.tools.list_ports.comports()
                     if p.vid is not None]
        except ImportError:
            pass
    return sorted(set(ports))


def _port_label(port):
    """Return a display suffix for a serial port (e.g. ' (JTAG)' for usbmodem)."""
    name = os.path.basename(port).lower()
    if "usbmodem" in name:
        return f"  {S.DIM}(USB JTAG/serial debug){S.RESET}"
    if "usbserial" in name or "slab" in name or "wchusbserial" in name:
        return f"  {S.DIM}(USB-serial adapter){S.RESET}"
    return ""


def select_port(prompt_extra=""):
    """Auto-detect and let user select a serial port. Returns port path or None."""
    while True:
        status("Scanning serial ports …", "work")
        ports = find_serial_ports()

        if not ports:
            status("No serial devices found", "err")
            action_box([
                "Make sure the TBD-16 is connected:",
                "",
                "Front JTAG port (USB-C #3) → your computer",
                "Back Port #1 or #2 → power",
            ])
            choice = ask("Enter port path, 'r' to scan again, or Enter to cancel")
            if not choice:
                return None
            if choice.lower() == "r":
                print()
                continue
            return choice

        if len(ports) == 1:
            label = _port_label(ports[0])
            status(f"Found port → {S.CYAN}{ports[0]}{S.RESET}{label}", "ok")
            if ask("Use this port? (y/n)", "y").lower() == "y":
                return ports[0]

    status(f"Found {len(ports)} port(s):\n", "ok")
    for i, p in enumerate(ports, 1):
        label = _port_label(p)
        print(f"      {S.GREEN}{S.BOLD}[{i}]{S.RESET}  {p}{label}")
    print()

    if prompt_extra:
        status(prompt_extra, "info")
    status("Select the port for the front JTAG port (USB-C #3)", "info")
    choice = ask("Port number", "1")
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(ports):
            return ports[idx]
    except ValueError:
        if choice.startswith("/dev/") or choice.startswith("COM"):
            return choice

    status("Invalid selection", "err")
    return None


# ═══════════════════════════════════════════════════
#  PARTITION TABLE READING & OTA DATA
# ═══════════════════════════════════════════════════
def parse_partition_table(data):
    """Parse ESP-IDF binary partition table.

    Format per entry (32 bytes): <2sBBII16sI
      magic(2) type(1) subtype(1) offset(4) size(4) name(16) flags(4)
    """
    entries = []
    for i in range(0, len(data), PT_ENTRY_SIZE):
        entry = data[i:i + PT_ENTRY_SIZE]
        if len(entry) < PT_ENTRY_SIZE:
            break
        magic = entry[0:2]
        if magic == PT_MD5_MAGIC:
            break
        if magic != PT_MAGIC:
            break
        type_id = entry[2]
        subtype = entry[3]
        offset = struct.unpack_from('<I', entry, 4)[0]
        size = struct.unpack_from('<I', entry, 8)[0]
        name = entry[12:28].split(b'\x00')[0].decode('ascii', errors='replace')
        entries.append({
            'type': type_id,
            'subtype': subtype,
            'offset': offset,
            'size': size,
            'name': name,
        })
    return entries


def read_partition_table(esptool_cmd, port):
    """Read partition table from device flash via esptool read_flash.

    Returns list of partition entries or None on failure.
    """
    pt_file = os.path.join(tempfile.gettempdir(), "tbd_partition_table.bin")
    cmd = esptool_cmd + [
        "--chip", CHIP,
        "--port", port,
        "--baud", BAUD,
        "--before", "default-reset",
        "--after", "no-reset",
        "read-flash",
        hex(PT_ADDR), "0xC00",
        pt_file,
    ]

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        if r.returncode != 0:
            return None
        with open(pt_file, 'rb') as f:
            data = f.read()
        try:
            os.unlink(pt_file)
        except OSError:
            pass
        return parse_partition_table(data)
    except Exception:
        try:
            os.unlink(pt_file)
        except OSError:
            pass
        return None


def detect_ota1_address(esptool_cmd, port):
    """Read partition table from device and find ota_1 address.

    Returns (offset, size) tuple or None if ota_1 not found.
    """
    status("Reading partition table from device …", "work")
    entries = read_partition_table(esptool_cmd, port)
    if not entries:
        return None

    # Show what we found (useful for debugging)
    app_parts = [e for e in entries if e['type'] == 0]  # type 0 = app
    for e in app_parts:
        status(f"  Partition: {e['name']}  "
               f"offset=0x{e['offset']:x}  size={e['size'] // 1024} KB", "info")

    # Find ota_1: type=0 (app), subtype=0x11 (ota_1)
    for e in entries:
        if e['type'] == 0 and e['subtype'] == 0x11:
            return (e['offset'], e['size'])
        if e['name'] == 'ota_1':
            return (e['offset'], e['size'])

    return None


def build_ota_data(slot):
    """Build 8 KB OTA data blob that selects the given boot slot.

    Matches ESP-IDF esp_ota_select_entry_t exactly:
      struct { uint32_t ota_seq; uint8_t seq_label[20];
               uint32_t ota_state; uint32_t crc; }  // 32 bytes

    CRC = binascii.crc32(packed_seq, 0xFFFFFFFF)  — over ota_seq only (4 bytes).
    This matches esp_rom_crc32_le(UINT32_MAX, &s->ota_seq, 4) in the bootloader.

    slot=None → all 0xFF (erased state, boots factory/ota_0)
    slot=0 → seq=1 → boots ota_0
    slot=1 → seq=2 → boots ota_1
    """
    data = bytearray(b'\xff' * OTA_DATA_SIZE)
    if slot is None:
        return bytes(data)

    seq = slot + 1
    seq_bytes = struct.pack('<I', seq)
    crc = zlib.crc32(seq_bytes, 0xFFFFFFFF) & 0xFFFFFFFF

    # Write OTA select entry at offset 0 (first 4 KB sector)
    struct.pack_into('<I', data, 0, seq)        # ota_seq
    # bytes 4..23:  seq_label — stays 0xFF
    # bytes 24..27: ota_state — stays 0xFF (ESP_OTA_IMG_NEW)
    struct.pack_into('<I', data, 28, crc)       # CRC
    # Second sector (offset 0x1000) stays all 0xFF → invalid entry

    return bytes(data)


# ═══════════════════════════════════════════════════
#  ESP32-P4 FLASHING
# ═══════════════════════════════════════════════════
def flash_msc_mode(esptool_cmd, port, msc_path):
    """Flash MSC firmware to ota_1 and switch boot to it.

    1. Reads partition table from device to find ota_1 address dynamically
    2. Falls back to known address (0x510000) if partition read fails
    3. Writes OTA data + MSC firmware in a single esptool write_flash call
    4. Hard-resets so the device boots into SD-card USB mode
    """
    if not os.path.exists(msc_path):
        status(f"MSC firmware not found: {msc_path}", "err")
        return False

    # ── Detect ota_1 address from device partition table ──
    ota1_addr = None
    ota1_size = None
    detected = detect_ota1_address(esptool_cmd, port)

    if detected:
        ota1_addr, ota1_size = detected
        status(f"ota_1 partition detected at 0x{ota1_addr:x} "
               f"({ota1_size // 1024} KB)", "ok")

        # Sanity check: MSC firmware must fit in the partition
        msc_size = os.path.getsize(msc_path)
        if msc_size > ota1_size:
            status(f"MSC firmware ({msc_size} bytes) exceeds ota_1 partition "
                   f"({ota1_size} bytes)!", "err")
            return False
    else:
        ota1_addr = FALLBACK_OTA1_ADDR
        status(f"Could not read partition table — using known address "
               f"0x{ota1_addr:x}", "warn")

    # ── Build OTA data blob (select ota_1 = slot 1) ──
    ota_data = build_ota_data(1)
    ota_data_path = os.path.join(tempfile.gettempdir(), "ota_data_msc.bin")
    with open(ota_data_path, 'wb') as f:
        f.write(ota_data)

    size_mb = os.path.getsize(msc_path) / 1048576
    ota1_hex = "0x%x" % ota1_addr
    ota_hex = "0x%x" % OTA_DATA_ADDR

    print()
    status(f"MSC firmware → {os.path.basename(msc_path)}  ({size_mb:.2f} MB)", "info")
    status(f"Port         → {port}", "info")
    status(f"ota_1 offset → {ota1_hex}", "info")
    status(f"OTA data     → {ota_hex} (switch boot to ota_1)", "info")
    print()

    cmd = esptool_cmd + [
        "--chip", CHIP,
        "--port", port,
        "--baud", BAUD,
        "--before", "default-reset",
        "--after", "hard-reset",
        "write-flash",
        "-z",
        "--flash-mode", "dio",
        "--flash-freq", "80m",
        "--flash-size", "detect",
        ota_hex, ota_data_path,
        ota1_hex, msc_path,
    ]

    print(f"  {S.DIM}$ {' '.join(cmd)}{S.RESET}\n")
    separator()
    print()

    try:
        rc = subprocess.run(cmd).returncode
        print()
        separator()
        try:
            os.unlink(ota_data_path)
        except OSError:
            pass
        if rc == 0:
            status("MSC firmware flashed — device will reboot into SD card mode", "ok")
            return True
        status("MSC firmware flash failed — check errors above", "err")
    except Exception as e:
        status(f"Error: {e}", "err")
    return False


def flash_p4(esptool_cmd, port, firmware_path, offset=UNIFIED_OFFSET, label="P4 firmware"):
    """Flash a binary to the ESP32-P4 via esptool. Returns True on success.

    Note: The unified binary (offset 0x0) already includes the OTA data region
    at 0xd000 in erased state (all 0xFF = boot ota_0). No separate OTA write
    is needed — it would cause an overlap error in esptool.
    """
    if not os.path.exists(firmware_path):
        status(f"Firmware file not found: {firmware_path}", "err")
        return False

    size_mb = os.path.getsize(firmware_path) / 1048576
    status(f"File     → {os.path.basename(firmware_path)}  ({size_mb:.2f} MB)", "info")
    status(f"Port     → {port}", "info")
    status(f"Offset   → {offset}", "info")
    print()

    cmd = esptool_cmd + [
        "--chip", CHIP,
        "--port", port,
        "--baud", BAUD,
        "--before", "default-reset",
        "--after", "hard-reset",
        "write-flash",
        "-z",
        "--flash-mode", "dio",
        "--flash-freq", "80m",
        "--flash-size", "detect",
        offset, firmware_path,
    ]

    print(f"  {S.DIM}$ {' '.join(cmd)}{S.RESET}\n")
    separator()
    print()

    try:
        rc = subprocess.run(cmd).returncode
        print()
        separator()
        if rc == 0:
            status(f"{label} flashed successfully!", "ok")
            return True
        status(f"Flashing {label} failed — check errors above", "err")
    except Exception as e:
        status(f"Error: {e}", "err")
    return False


# ═══════════════════════════════════════════════════
#  RP2350 (PICO) — UF2 FLASHING
# ═══════════════════════════════════════════════════
def find_uf2_volume():
    """Find a mounted UF2 bootloader volume (RP2350 in BOOTSEL mode)."""
    osname = platform.system()

    if osname == "Darwin":
        bases = ["/Volumes"]
    elif osname == "Linux":
        user = os.environ.get("USER", "")
        bases = [f"/media/{user}", f"/run/media/{user}", "/mnt"]
    elif osname == "Windows":
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            vol = f"{letter}:\\"
            if os.path.isfile(os.path.join(vol, "INFO_UF2.TXT")):
                return vol
        return None
    else:
        return None

    for base in bases:
        if not os.path.isdir(base):
            continue
        try:
            for name in os.listdir(base):
                vol = os.path.join(base, name)
                if os.path.isdir(vol) and os.path.isfile(os.path.join(vol, "INFO_UF2.TXT")):
                    return vol
        except OSError:
            pass
    return None


def wait_for_uf2_volume(timeout=UF2_MOUNT_TIMEOUT):
    """Wait for a UF2 volume to appear. Returns mount path or None."""
    status("Waiting for RP2350 BOOTSEL volume …", "work")
    start = time.time()
    while time.time() - start < timeout:
        vol = find_uf2_volume()
        if vol:
            print()
            status(f"Found UF2 volume → {vol}", "ok")
            return vol
        elapsed = int(time.time() - start)
        sys.stdout.write(f"\r  {S.CYAN}⟳{S.RESET} Scanning … "
                         f"({elapsed}s / {timeout}s)  ")
        sys.stdout.flush()
        time.sleep(1)

    print()
    status("UF2 volume not found within timeout", "err")
    return None


def flash_uf2(uf2_path, volume):
    """Copy a UF2 file to a BOOTSEL volume. Returns True on success."""
    dest = os.path.join(volume, os.path.basename(uf2_path))
    status(f"Copying {os.path.basename(uf2_path)} → {volume}/", "work")

    try:
        shutil.copy2(uf2_path, dest)
        subprocess.run(["sync"], timeout=10, capture_output=True)
        time.sleep(2)
        status("UF2 copied — RP2350 will reboot automatically", "ok")
        return True
    except Exception as e:
        status(f"Failed to copy UF2: {e}", "err")
        return False


def wizard_flash_pico(urls, cache_dir):
    """Guide user through RP2350 BOOTSEL flashing. Returns True on success."""
    pico_url = urls.get("pico_url")
    if not pico_url:
        status("No Pico firmware available for this version — skipping", "info")
        return True

    print()
    status(f"{S.BOLD}Flash RP2350 (Pico) Firmware{S.RESET}", "step")
    action_box([
        "1. Hold the BOOTSEL button (left of JTAG port)",
        "2. While holding, plug back USB-C Port #2",
        "   (closest to the edge)",
        "3. Release the BOOTSEL button",
        "4. A drive 'RP2350' or 'RPI-RP2' should appear",
        "",
        "You can disconnect the front JTAG cable.",
    ])

    if ask("Ready? (y/n)", "y").lower() != "y":
        status("Skipping Pico flash", "warn")
        return True

    # Download UF2
    uf2_name = os.path.basename(pico_url)
    uf2_path = os.path.join(cache_dir, uf2_name)
    if not os.path.exists(uf2_path):
        if not download_file(pico_url, uf2_path, "Pico firmware"):
            return False
    else:
        status(f"Using cached: {uf2_name}", "ok")

    # Find or wait for volume — with retry
    while True:
        vol = find_uf2_volume()
        if not vol:
            print()
            status("No UF2 volume detected yet.", "warn")
            status("Make sure RP2350 is in BOOTSEL mode:", "info")
            status("  Hold BOOTSEL button → plug USB-C Port #2 → release button", "info")
            print()

            if ask("Wait for volume? (y/n)", "y").lower() != "y":
                status("Skipping Pico flash", "warn")
                return True
            vol = wait_for_uf2_volume()
            if not vol:
                if not _retry_prompt("Pico flash", PICO_RECOVERY_TIPS):
                    return False
                continue

        if flash_uf2(uf2_path, vol):
            return True

        if not _retry_prompt("UF2 copy", PICO_RECOVERY_TIPS):
            return False


# ═══════════════════════════════════════════════════
#  SD CARD OPERATIONS
# ═══════════════════════════════════════════════════
def _is_removable_volume(vol_path):
    """Check if a volume is removable/external media (not a system disk).

    On macOS: uses diskutil to verify the volume is on removable/external media.
    On Linux: checks if the device is under /media or /run/media (auto-mounted).
    """
    osname = platform.system()

    if osname == "Darwin":
        try:
            r = subprocess.run(
                ["diskutil", "info", vol_path],
                capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return False
            info = r.stdout
            # Check it's removable or an external disk
            removable = False
            for line in info.splitlines():
                line = line.strip()
                if line.startswith("Removable Media:") and "Yes" in line:
                    removable = True
                if line.startswith("Protocol:") and "USB" in line:
                    removable = True
                if line.startswith("Virtual:") and "Yes" in line:
                    return False  # Never erase virtual disks
            return removable
        except Exception:
            return False
    elif osname == "Linux":
        rp = os.path.realpath(vol_path)
        return rp.startswith("/media/") or rp.startswith("/run/media/")
    elif osname == "Windows":
        # On Windows, check if it's a removable drive
        try:
            import ctypes
            drive = os.path.splitdrive(vol_path)[0] + "\\"
            return ctypes.windll.kernel32.GetDriveTypeW(drive) == 2  # DRIVE_REMOVABLE
        except Exception:
            return False
    return False


def _get_volume_info(vol_path):
    """Get human-readable volume information for display.

    Returns dict with 'name', 'filesystem', 'size', 'device' keys.
    """
    info = {
        'name': os.path.basename(vol_path) or vol_path,
        'filesystem': 'unknown',
        'size': 'unknown',
        'device': 'unknown',
    }

    if platform.system() == "Darwin":
        try:
            r = subprocess.run(
                ["diskutil", "info", vol_path],
                capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("Volume Name:"):
                        info['name'] = line.split(":", 1)[1].strip()
                    elif line.startswith("File System Personality:"):
                        info['filesystem'] = line.split(":", 1)[1].strip()
                    elif line.startswith("Type (Bundle):"):
                        info['filesystem'] = line.split(":", 1)[1].strip()
                    elif line.startswith("Disk Size:"):
                        info['size'] = line.split(":", 1)[1].strip().split("(")[0].strip()
                    elif line.startswith("Device Node:"):
                        info['device'] = line.split(":", 1)[1].strip()
        except Exception:
            pass
    elif platform.system() == "Linux":
        try:
            r = subprocess.run(
                ["df", "-h", vol_path],
                capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                lines = r.stdout.strip().splitlines()
                if len(lines) >= 2:
                    parts = lines[1].split()
                    info['device'] = parts[0]
                    info['size'] = parts[1]
        except Exception:
            pass
    elif platform.system() == "Windows":
        drive_letter = os.path.splitdrive(vol_path)[0]
        if drive_letter:
            info['device'] = drive_letter
            try:
                r = subprocess.run(
                    ["cmd", "/c", "vol", drive_letter],
                    capture_output=True, text=True, timeout=5)
                for line in r.stdout.splitlines():
                    if "is" in line.lower():
                        info['name'] = line.split("is", 1)[-1].strip()
            except Exception:
                pass
            try:
                r = subprocess.run(
                    ["wmic", "logicaldisk", "where",
                     f"DeviceID='{drive_letter}'", "get", "Size,FileSystem",
                     "/format:list"],
                    capture_output=True, text=True, timeout=5)
                for line in r.stdout.splitlines():
                    if line.startswith("FileSystem="):
                        info['filesystem'] = line.split("=", 1)[1].strip()
                    elif line.startswith("Size="):
                        try:
                            sz = int(line.split("=", 1)[1].strip())
                            info['size'] = f"{sz / 1073741824:.1f} GB"
                        except ValueError:
                            pass
            except Exception:
                pass

    return info


def _is_safe_to_erase(vol_path):
    """Multi-layer safety check before erasing a volume.

    Returns (safe: bool, reason: str).
    """
    real_path = os.path.realpath(vol_path)

    # Layer 1: Never erase protected system paths
    for protected in PROTECTED_PATHS:
        if real_path == protected or real_path.rstrip("/\\") == protected.rstrip("/\\"):
            return False, f"Protected system path: {real_path}"

    # Layer 2: Never erase root or its direct children
    if real_path == "/" or (real_path.startswith("/") and real_path.count("/") == 1):
        return False, f"System directory: {real_path}"

    # Layer 3: On macOS, must be under /Volumes/ (not /Volumes/Macintosh HD)
    if platform.system() == "Darwin":
        if not real_path.startswith("/Volumes/"):
            return False, f"Not a mounted volume: {real_path}"
        vol_name = real_path.replace("/Volumes/", "").split("/")[0]
        if vol_name in ("Macintosh HD", "Macintosh HD - Data", ""):
            return False, f"System volume: {vol_name}"

    # Layer 4: Must be removable media (USB disk, SD card reader)
    if not _is_removable_volume(vol_path):
        return False, "Not removable media — refusing to erase non-removable volumes"

    # Layer 5: Path must be a directory that exists
    if not os.path.isdir(vol_path):
        return False, f"Not a directory: {vol_path}"

    return True, "OK"


def find_sd_card():
    """Find the TBD-16's SD card mount point.

    Uses strict matching:
    1. Check known volume names (NO NAME)
    2. Check for TBD-16 signature files (data/spm-config.json)
    3. Always validate the volume is on removable media
    """
    osname = platform.system()

    candidates = []

    if osname == "Darwin":
        # Priority 1: Known volume names
        for name in SD_VOLUME_NAMES:
            vol = f"/Volumes/{name}"
            if os.path.isdir(vol):
                candidates.append(vol)

        # Priority 2: Look for TBD-16 signature files on removable volumes only
        try:
            for name in os.listdir("/Volumes"):
                vol = f"/Volumes/{name}"
                if vol in candidates:
                    continue
                if not os.path.isdir(vol):
                    continue
                if os.path.isfile(os.path.join(vol, "INFO_UF2.TXT")):
                    continue  # RP2350 BOOTSEL volume, not SD card
                # Must have TBD-16 specific signature
                if (os.path.isfile(os.path.join(vol, "data", "spm-config.json"))
                        or os.path.isfile(os.path.join(vol, ".version"))):
                    candidates.append(vol)
        except OSError:
            pass

    elif osname == "Linux":
        user = os.environ.get("USER", "")
        for base in [f"/media/{user}", f"/run/media/{user}", "/mnt"]:
            if not os.path.isdir(base):
                continue
            try:
                for name in os.listdir(base):
                    vol = os.path.join(base, name)
                    if not os.path.isdir(vol):
                        continue
                    if name in SD_VOLUME_NAMES:
                        candidates.append(vol)
                    elif (os.path.isfile(os.path.join(vol, "data", "spm-config.json"))
                            or os.path.isfile(os.path.join(vol, ".version"))):
                        candidates.append(vol)
            except OSError:
                pass

    elif osname == "Windows":
        # Scan removable drive letters
        try:
            import ctypes
            for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
                drive = f"{letter}:\\"
                if ctypes.windll.kernel32.GetDriveTypeW(drive) == 2:  # DRIVE_REMOVABLE
                    if os.path.isdir(drive):
                        # Check volume label via vol command
                        label = ""
                        try:
                            r = subprocess.run(
                                ["cmd", "/c", "vol", f"{letter}:"],
                                capture_output=True, text=True, timeout=5)
                            for line in r.stdout.splitlines():
                                if "is" in line.lower():
                                    label = line.split("is", 1)[-1].strip()
                        except Exception:
                            pass
                        if label in SD_VOLUME_NAMES:
                            candidates.append(drive)
                        elif (os.path.isfile(os.path.join(drive, "data", "spm-config.json"))
                                or os.path.isfile(os.path.join(drive, ".version"))):
                            candidates.append(drive)
        except Exception:
            pass

    # Filter: must be removable media
    for vol in candidates:
        if _is_removable_volume(vol):
            return vol

    return None


def wait_for_sd_card(timeout=SD_MOUNT_TIMEOUT):
    """Wait for SD card to mount. Returns mount path or None."""
    status("Waiting for SD card to mount …", "work")
    status("(This can take 20–30 seconds after MSC firmware flash)", "info")
    start = time.time()
    while time.time() - start < timeout:
        vol = find_sd_card()
        if vol:
            print()
            status(f"SD card found → {vol}", "ok")
            time.sleep(2)
            return vol
        elapsed = int(time.time() - start)
        sys.stdout.write(f"\r  {S.CYAN}⟳{S.RESET} Scanning … "
                         f"({elapsed}s / {timeout}s)  ")
        sys.stdout.flush()
        time.sleep(2)

    print()
    status("SD card not found within timeout", "err")
    return None


def erase_sd_card(mount_point):
    """Erase all user files on the SD card. Returns True on success.

    Safety: validates mount_point is a removable volume before erasing.
    Handles macOS filesystem metadata (.Spotlight-V100, .fseventsd) that
    can become read-only on FAT volumes.
    """
    # SAFETY GATE: Multi-layer validation before any deletion
    safe, reason = _is_safe_to_erase(mount_point)
    if not safe:
        status(f"REFUSED to erase: {reason}", "err")
        return False

    vol_info = _get_volume_info(mount_point)
    status(f"Erasing SD card: {vol_info['name']} ({vol_info['filesystem']}, {vol_info['size']}) on {vol_info['device']}", "work")

    errors = []
    try:
        for item in os.listdir(mount_point):
            path = os.path.join(mount_point, item)
            # Skip macOS system metadata — these can be read-only on FAT
            if item in MACOS_SYSTEM_DIRS or item.startswith("._"):
                continue
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
            except PermissionError:
                # FAT filesystem state issue (e.g. .Spotlight-V100 becoming
                # immutable, or macOS metadata we didn't skip)
                errors.append(f"SKIP {item}: permission denied (filesystem state)")
            except OSError as e:
                errors.append(f"SKIP {item}: {e}")

        if errors:
            for e in errors:
                status(e, "warn")
            status("SD card erased (some metadata files skipped — this is normal)", "ok")
        else:
            status("SD card erased", "ok")
        return True
    except Exception as e:
        status(f"Error erasing SD card: {e}", "err")
        return False


def extract_sd_image(zip_path, mount_point):
    """Extract SD card zip to mount point. Returns True on success."""
    status(f"Extracting SD card image to {mount_point} …", "work")

    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Verify integrity before extraction (catches corrupted downloads)
            bad = zf.testzip()
            if bad is not None:
                status(f"Zip integrity check failed — corrupted entry: {bad}", "err")
                status("Delete the cached file and re-download:", "info")
                status(f"  rm {zip_path}", "info")
                return False

            members = zf.namelist()
            total = len(members)

            # Check if all files share a single top-level directory
            top_dirs = set()
            for m in members:
                parts = m.split("/")
                if len(parts) > 1:
                    top_dirs.add(parts[0])

            strip_prefix = ""
            if len(top_dirs) == 1:
                prefix = list(top_dirs)[0] + "/"
                if all(m.startswith(prefix) or m.rstrip("/") == list(top_dirs)[0]
                       for m in members):
                    strip_prefix = prefix

            extracted = 0
            for i, member in enumerate(members):
                if i % 20 == 0 or i == total - 1:
                    progress_bar(i + 1, total)

                target_name = member
                if strip_prefix and member.startswith(strip_prefix):
                    target_name = member[len(strip_prefix):]

                if not target_name:
                    continue

                # Zip slip protection: ensure extracted path stays within mount_point
                target_path = os.path.normpath(os.path.join(mount_point, target_name))
                mount_real = os.path.normpath(mount_point)
                if not (target_path.startswith(mount_real + os.sep) or target_path == mount_real):
                    status(f"SECURITY: Skipping path-traversal zip entry: {member}", "warn")
                    continue

                if member.endswith("/"):
                    os.makedirs(target_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with zf.open(member) as src, open(target_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    extracted += 1

            print()
            status(f"Extracted {extracted} files to {mount_point}", "ok")
            return True

    except zipfile.BadZipFile:
        print()
        status("Downloaded file is not a valid zip — try re-downloading", "err")
    except Exception as e:
        print()
        status(f"Extraction failed: {e}", "err")
    return False


def clean_macos_dotfiles(mount_point):
    """Recursively delete macOS AppleDouble (._*) files from the SD card.

    macOS creates ._* resource-fork files on FAT volumes whenever files are
    written. The ESP32 firmware parses every .json it finds on the SD card —
    these ._*.json files cause JSON parse errors and Guru Meditation crashes.
    This MUST run after every extraction/write to the SD card.
    """
    removed = 0
    for dirpath, _dirnames, filenames in os.walk(mount_point):
        for fname in filenames:
            if fname.startswith("._"):
                try:
                    os.unlink(os.path.join(dirpath, fname))
                    removed += 1
                except OSError:
                    pass
        # Also remove __MACOSX directories if they leaked through
        macosx_dir = os.path.join(dirpath, "__MACOSX")
        if os.path.isdir(macosx_dir):
            try:
                shutil.rmtree(macosx_dir)
                removed += 1
            except OSError:
                pass
    if removed:
        status(f"Cleaned {removed} macOS metadata file(s) from SD card", "ok")
    else:
        status("No macOS metadata files to clean", "ok")
    return True


def write_hash_file(hash_url, mount_point, cache_dir):
    """Download hash and write .version + hash file to SD card."""
    if not hash_url:
        return True

    hash_name = os.path.basename(hash_url)
    hash_local = os.path.join(cache_dir, hash_name)

    if not download_file(hash_url, hash_local, "SD card hash"):
        status("Could not download hash — skipping .version write", "warn")
        return True

    try:
        with open(hash_local, "r") as f:
            hash_content = f.read().strip()

        for target in ["dada-tbd-sd-hash.txt", ".version"]:
            with open(os.path.join(mount_point, target), "w") as f:
                f.write(hash_content + "\n")

        status("Hash written to .version and dada-tbd-sd-hash.txt", "ok")
        return True
    except Exception as e:
        status(f"Error writing hash files: {e}", "warn")
        return True


def eject_sd_card(mount_point):
    """Safely eject the SD card."""
    status(f"Ejecting {mount_point} …", "work")
    try:
        if platform.system() != "Windows":
            subprocess.run(["sync"], timeout=10, capture_output=True)
        time.sleep(1)

        if platform.system() == "Darwin":
            r = subprocess.run(
                ["diskutil", "eject", mount_point],
                capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                status("SD card ejected", "ok")
                return True
            status(f"Eject warning: {r.stderr.strip()}", "warn")
        elif platform.system() == "Linux":
            r = subprocess.run(
                ["umount", mount_point],
                capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                status("SD card unmounted", "ok")
                return True
            status(f"Unmount warning: {r.stderr.strip()}", "warn")
        elif platform.system() == "Windows":
            # Use PowerShell to eject removable drive
            drive_letter = os.path.splitdrive(mount_point)[0]
            if drive_letter:
                r = subprocess.run(
                    ["powershell", "-Command",
                     f"(New-Object -ComObject Shell.Application).Namespace(17).ParseName('{drive_letter}\\').InvokeVerb('Eject')"],
                    capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    status("SD card ejected", "ok")
                    return True
                status(f"Eject warning: {r.stderr.strip()}", "warn")
    except Exception as e:
        status(f"Eject error: {e}", "warn")

    status("Please eject the SD card manually before continuing", "warn")
    ask("Press Enter once ejected")
    return True


# ═══════════════════════════════════════════════════
#  WIZARD: QUICK UPDATE
# ═══════════════════════════════════════════════════
def wizard_quick(channel="stable", version=None, is_cli=False):
    """Quick Update wizard: Flash P4 + Pico (no SD card erase)."""
    clear()
    banner()
    header(f"⚡ Quick Update — {_channel_label(channel)}")

    cache_dir = ensure_cache_dir()

    # ── Select version ──
    if version is None:
        status("Fetching available versions …", "work")
        catalog = fetch_releases(channel)
        if not catalog:
            return False
        version = select_version(catalog, channel)
        if not version:
            return False

    urls = build_urls(version, channel)
    tag = urls["tag"]
    has_pico = urls["pico_url"] is not None
    total_steps = 3 if has_pico else 2

    print()
    status(f"Selected: {S.BOLD}{tag}{S.RESET}", "ok")
    if has_pico:
        status("Will flash: ESP32-P4 + RP2350 (Pico)", "info")
    else:
        status("Will flash: ESP32-P4 only (no Pico firmware for this version)", "info")

    # ── Step 1: Prerequisites ──
    step_header(1, total_steps, "Prerequisites")
    if not check_python():
        return False
    esptool_cmd = ensure_esptool()
    if not esptool_cmd:
        return False

    # ── Step 2: Flash ESP32-P4 ──
    step_header(2, total_steps, "Flash ESP32-P4 Firmware")

    action_box([
        "Connect front JTAG port (USB-C #3) → computer",
        "Connect back Port #1 or #2 → power",
    ])

    if not is_cli:
        if ask("Ready? (y/n)", "y").lower() != "y":
            return False

    p4_name = os.path.basename(urls["p4_url"])
    p4_path = os.path.join(cache_dir, p4_name)
    if not os.path.exists(p4_path):
        if not download_file(urls["p4_url"], p4_path, "P4 firmware"):
            return False
    else:
        size_mb = os.path.getsize(p4_path) / 1048576
        status(f"Using cached firmware: {p4_name} ({size_mb:.2f} MB)", "ok")

    port = select_port()
    if not port:
        return False

    print()
    while not flash_p4(esptool_cmd, port, p4_path, UNIFIED_OFFSET, "P4 firmware"):
        if not _retry_prompt("P4 flash", FLASH_RECOVERY_TIPS):
            return False
        port = select_port("Re-detecting ports for retry")
        if not port:
            return False
        print()

    # ── Step 3: Flash RP2350 ──
    if has_pico:
        step_header(3, total_steps, "Flash RP2350 (Pico)")
        if not wizard_flash_pico(urls, cache_dir):
            status("Pico flash had issues, but P4 was updated successfully", "warn")

    # ── Done ──
    completion_banner("Quick Update Complete!", tag)
    power_cycle_warning()
    return True


# ═══════════════════════════════════════════════════
#  WIZARD: FULL SD CARD DEPLOY
# ═══════════════════════════════════════════════════
def wizard_full(channel="stable", version=None, is_cli=False):
    """Full SD Deploy wizard: MSC/reader → SD image → P4 + Pico."""
    clear()
    banner()
    header(f"🗄️  Full SD Card Deploy — {_channel_label(channel)}")

    cache_dir = ensure_cache_dir()

    # ── Select version ──
    if version is None:
        status("Fetching available versions …", "work")
        catalog = fetch_releases(channel)
        if not catalog:
            return False
        version = select_version(catalog, channel)
        if not version:
            return False

    urls = build_urls(version, channel)
    tag = urls["tag"]
    has_pico = urls["pico_url"] is not None

    print()
    status(f"Selected: {S.BOLD}{tag}{S.RESET}", "ok")

    # ── Warn about data loss ──
    print()
    print(f"  {S.RED}┌─────────────────────────────────────────────────────────┐{S.RESET}")
    print(f"  {S.RED}│{S.RESET}  {S.RED}{S.BOLD}⚠  WARNING: This will ERASE ALL data on the SD card!{S.RESET}  {S.RED}│{S.RESET}")
    print(f"  {S.RED}│{S.RESET}                                                         {S.RED}│{S.RESET}")
    print(f"  {S.RED}│{S.RESET}  Samples, macros, presets, and custom files              {S.RED}│{S.RESET}")
    print(f"  {S.RED}│{S.RESET}  will be deleted. A fresh factory SD card image          {S.RED}│{S.RESET}")
    print(f"  {S.RED}│{S.RESET}  will be written.                                       {S.RED}│{S.RESET}")
    print(f"  {S.RED}└─────────────────────────────────────────────────────────┘{S.RESET}")
    print()

    if not is_cli:
        if ask("Type 'yes' to continue", "no").lower() != "yes":
            status("Cancelled", "info")
            return False

    # ── SD card access method ──
    print()
    status("How would you like to access the SD card?", "info")
    print()
    print(f"      {S.GREEN}{S.BOLD}[1]{S.RESET}  Via USB (MSC mode)    "
          f"{S.DIM}— no need to open the device{S.RESET}  {S.GREEN}◄{S.RESET}")
    print(f"      {S.YELLOW}{S.BOLD}[2]{S.RESET}  External card reader  "
          f"{S.DIM}— requires opening the device{S.RESET}")
    print()
    use_msc = ask("Select method", "1") != "2"

    # Calculate steps
    total_steps = (4 if use_msc else 3) + (1 if has_pico else 0)
    step_n = 0

    # ── Prerequisites ──
    step_n += 1
    step_header(step_n, total_steps, "Prerequisites")
    if not check_python():
        return False
    esptool_cmd = ensure_esptool()
    if not esptool_cmd:
        return False

    # ── Mount SD card ──
    sd_mount = None

    if use_msc:
        step_n += 1
        step_header(step_n, total_steps, "Flash MSC Firmware & Mount SD Card")

        action_box([
            "Connect front JTAG port (USB-C #3) → computer",
            "Connect back Port #1 → computer",
            "  (power + SD card access)",
        ])

        if not is_cli:
            if ask("Ready? (y/n)", "y").lower() != "y":
                return False

        # Download MSC firmware
        msc_name = os.path.basename(urls["msc_url"])
        msc_path = os.path.join(cache_dir, msc_name)
        if not os.path.exists(msc_path):
            if not download_file(urls["msc_url"], msc_path, "MSC firmware"):
                return False
        else:
            status(f"Using cached MSC firmware: {msc_name}", "ok")

        port = select_port()
        if not port:
            return False

        print()
        while not flash_msc_mode(esptool_cmd, port, msc_path):
            if not _retry_prompt("MSC flash", FLASH_RECOVERY_TIPS + [
                "Or cancel and use an external card reader instead (option 2)",
            ]):
                return False
            port = select_port("Re-detecting ports for retry")
            if not port:
                return False
            print()

        # Wait for SD card
        print()
        status("Device is rebooting into SD card mode …", "work")
        time.sleep(POST_FLASH_DELAY)

        sd_mount = wait_for_sd_card()
        while not sd_mount:
            if not _retry_prompt("SD card mount", [
                "Check back Port #1 is connected to your computer",
                "Unplug and replug back Port #1, wait 30 seconds",
                "Try a different USB port on your computer",
            ]):
                return False
            sd_mount = wait_for_sd_card()
    else:
        step_n += 1
        step_header(step_n, total_steps, "Mount SD Card via Card Reader")

        action_box([
            "1. Power off the TBD-16 (disconnect all cables)",
            "2. Open the device and remove the SD card",
            "3. Insert the SD card into your card reader",
        ])
        ask("Press Enter when the SD card is mounted")

        sd_mount = find_sd_card()
        if not sd_mount:
            sd_mount = ask("Enter the SD card mount path (e.g. /Volumes/NO NAME)")
            if not os.path.isdir(sd_mount):
                status(f"Directory not found: {sd_mount}", "err")
                return False

    # ── Write SD card image ──
    step_n += 1
    step_header(step_n, total_steps, "Write SD Card Image")

    vol_info = _get_volume_info(sd_mount)
    status(f"SD card → {sd_mount}", "ok")
    status(f"  Volume:     {S.BOLD}{vol_info['name']}{S.RESET}", "info")
    status(f"  Filesystem: {vol_info['filesystem']}", "info")
    status(f"  Size:       {vol_info['size']}", "info")
    status(f"  Device:     {vol_info['device']}", "info")

    if not urls.get("sd_url"):
        status("No SD card image available for this version!", "err")
        return False

    sd_zip_name = os.path.basename(urls["sd_url"])
    sd_zip_path = os.path.join(cache_dir, sd_zip_name)
    if not os.path.exists(sd_zip_path):
        if not download_file(urls["sd_url"], sd_zip_path, "SD card image"):
            return False
    else:
        size_mb = os.path.getsize(sd_zip_path) / 1048576
        status(f"Using cached SD image: {sd_zip_name} ({size_mb:.1f} MB)", "ok")

    print()
    if not erase_sd_card(sd_mount):
        return False

    if not extract_sd_image(sd_zip_path, sd_mount):
        return False

    write_hash_file(urls.get("hash_url"), sd_mount, cache_dir)

    # Clean macOS ._* files — CRITICAL: these cause JSON parse errors
    # and Guru Meditation crashes on the ESP32 firmware
    print()
    status("Cleaning macOS metadata from SD card …", "work")
    clean_macos_dotfiles(sd_mount)

    print()
    eject_sd_card(sd_mount)

    # ── Flash P4 firmware ──
    step_n += 1
    step_header(step_n, total_steps, "Flash ESP32-P4 Firmware")

    if use_msc:
        action_box([
            "Power-cycle required before flashing:",
            "",
            "1. Unplug ALL back USB cables",
            "2. Wait 3 seconds",
            "3. Replug back port",
            "",
            "Keep front JTAG port connected.",
        ])
        ask("Press Enter when ready")

    p4_name = os.path.basename(urls["p4_url"])
    p4_path = os.path.join(cache_dir, p4_name)
    if not os.path.exists(p4_path):
        if not download_file(urls["p4_url"], p4_path, "P4 firmware"):
            return False
    else:
        size_mb = os.path.getsize(p4_path) / 1048576
        status(f"Using cached firmware: {p4_name} ({size_mb:.2f} MB)", "ok")

    print()
    port = select_port("Port may have changed after reboot — re-detecting")
    if not port:
        return False

    print()
    while not flash_p4(esptool_cmd, port, p4_path, UNIFIED_OFFSET, "P4 firmware"):
        if not _retry_prompt("P4 flash", FLASH_RECOVERY_TIPS):
            return False
        port = select_port("Re-detecting ports for retry")
        if not port:
            return False
        print()

    # ── Flash Pico ──
    if has_pico:
        step_n += 1
        step_header(step_n, total_steps, "Flash RP2350 (Pico)")
        if not wizard_flash_pico(urls, cache_dir):
            status("Pico flash had issues, but P4 and SD card were updated", "warn")

    # ── Done ──
    completion_banner("Full SD Card Deploy Complete!", tag)
    power_cycle_warning()
    return True


# ═══════════════════════════════════════════════════
#  INDIVIDUAL OPERATIONS (Menu items 3–5)
# ═══════════════════════════════════════════════════
def flash_p4_only(channel="stable"):
    """Flash just the ESP32-P4 firmware."""
    cache_dir = ensure_cache_dir()

    status("Fetching available versions …", "work")
    catalog = fetch_releases(channel)
    if not catalog:
        return False
    version = select_version(catalog, channel)
    if not version:
        return False

    urls = build_urls(version, channel)
    esptool_cmd = ensure_esptool()
    if not esptool_cmd:
        return False

    p4_name = os.path.basename(urls["p4_url"])
    p4_path = os.path.join(cache_dir, p4_name)
    if not os.path.exists(p4_path):
        if not download_file(urls["p4_url"], p4_path, "P4 firmware"):
            return False
    else:
        status(f"Using cached: {p4_name}", "ok")

    print()
    action_box([
        "Connect front JTAG port (USB-C #3) \u2192 computer",
        "Connect back Port #1 or #2 \u2192 power",
    ])

    port = select_port()
    if not port:
        return False

    print()
    while not flash_p4(esptool_cmd, port, p4_path, UNIFIED_OFFSET, "P4 firmware"):
        if not _retry_prompt("P4 flash", FLASH_RECOVERY_TIPS):
            return False
        port = select_port("Re-detecting ports for retry")
        if not port:
            return False
        print()
    completion_banner("ESP32-P4 Flash Complete!")
    power_cycle_warning()
    return True


def flash_pico_only(channel="stable"):
    """Flash just the RP2350 (Pico) firmware."""
    cache_dir = ensure_cache_dir()

    status("Fetching available versions …", "work")
    catalog = fetch_releases(channel)
    if not catalog:
        return False
    version = select_version(catalog, channel)
    if not version:
        return False

    urls = build_urls(version, channel)
    result = wizard_flash_pico(urls, cache_dir)
    if result:
        completion_banner("RP2350 (Pico) Flash Complete!")
        power_cycle_warning()
    return result


def deploy_sd_only(channel="stable"):
    """Deploy just the SD card image (no firmware flash)."""
    cache_dir = ensure_cache_dir()

    status("Fetching available versions …", "work")
    catalog = fetch_releases(channel)
    if not catalog:
        return False
    version = select_version(catalog, channel)
    if not version:
        return False

    urls = build_urls(version, channel)

    if not urls.get("sd_url"):
        status("No SD card image available for this version", "err")
        return False

    # SD access method
    print()
    status("How would you like to access the SD card?", "info")
    print()
    print(f"      {S.GREEN}{S.BOLD}[1]{S.RESET}  Via USB (MSC mode)   {S.GREEN}◄{S.RESET}")
    print(f"      {S.YELLOW}{S.BOLD}[2]{S.RESET}  External card reader")
    print()
    use_msc = ask("Select method", "1") != "2"

    sd_mount = None

    if use_msc:
        esptool_cmd = ensure_esptool()
        if not esptool_cmd:
            return False

        msc_name = os.path.basename(urls["msc_url"])
        msc_path = os.path.join(cache_dir, msc_name)
        if not os.path.exists(msc_path):
            if not download_file(urls["msc_url"], msc_path, "MSC firmware"):
                return False

        print()
        action_box([
            "Connect front JTAG port (USB-C #3) \u2192 computer",
            "Connect back Port #1 \u2192 computer",
            "  (power + SD card access)",
        ])

        port = select_port()
        if not port:
            return False

        print()
        while not flash_msc_mode(esptool_cmd, port, msc_path):
            if not _retry_prompt("MSC flash", FLASH_RECOVERY_TIPS):
                return False
            port = select_port("Re-detecting ports for retry")
            if not port:
                return False
            print()

        time.sleep(POST_FLASH_DELAY)
        sd_mount = wait_for_sd_card()
        while not sd_mount:
            if not _retry_prompt("SD card mount", [
                "Check back Port #1 is connected to your computer",
                "Unplug and replug back Port #1, wait 30 seconds",
            ]):
                return False
            sd_mount = wait_for_sd_card()
    else:
        action_box([
            "Insert SD card into your card reader",
        ])
        ask("Press Enter when mounted")
        sd_mount = find_sd_card()
        if not sd_mount:
            sd_mount = ask("Enter SD card mount path")
            if not os.path.isdir(sd_mount):
                status(f"Not found: {sd_mount}", "err")
                return False

    # Download SD image
    sd_zip_name = os.path.basename(urls["sd_url"])
    sd_zip_path = os.path.join(cache_dir, sd_zip_name)
    if not os.path.exists(sd_zip_path):
        if not download_file(urls["sd_url"], sd_zip_path, "SD card image"):
            return False

    vol_info = _get_volume_info(sd_mount)
    print()
    print(f"  {S.RED}{S.BOLD}⚠  This will ERASE ALL data on:{S.RESET}")
    print(f"     Volume:     {S.BOLD}{vol_info['name']}{S.RESET}")
    print(f"     Filesystem: {vol_info['filesystem']}")
    print(f"     Size:       {vol_info['size']}")
    print(f"     Device:     {vol_info['device']}")
    print(f"     Path:       {sd_mount}")
    print()
    if ask("Type 'yes' to continue", "no").lower() != "yes":
        return False

    if not erase_sd_card(sd_mount):
        return False
    if not extract_sd_image(sd_zip_path, sd_mount):
        return False
    write_hash_file(urls.get("hash_url"), sd_mount, cache_dir)

    # Clean macOS ._* files — CRITICAL: these cause JSON parse errors
    # and Guru Meditation crashes on the ESP32 firmware
    print()
    status("Cleaning macOS metadata from SD card …", "work")
    clean_macos_dotfiles(sd_mount)

    print()
    eject_sd_card(sd_mount)

    completion_banner("SD Card Image Deployed!")

    if use_msc:
        print(f"  {S.YELLOW}{S.BOLD}  ⚠  Device is still in MSC mode!{S.RESET}")
        print(f"  {S.YELLOW}  You MUST flash P4 firmware to restore normal boot.{S.RESET}")
        print(f"  {S.YELLOW}  Return to the menu and use [1] Quick Update or [3] Flash P4 Only{S.RESET}")
        print()

    return True


# ═══════════════════════════════════════════════════
#  MAIN MENU
# ═══════════════════════════════════════════════════
def main_menu():
    """Interactive main menu."""
    if platform.system() == "Windows":
        os.system("")

    while True:
        clear()
        banner()

        print(f"  {S.BOLD}What would you like to do?{S.RESET}")
        print(f"  {S.DIM}Enter a number to select, then press Enter{S.RESET}\n")

        print(f"      {S.GREEN}{S.BOLD}[1]{S.RESET}  {S.BOLD}⚡ Quick Update{S.RESET}  {S.GREEN}◄{S.RESET}")
        print(f"           {S.DIM}Flash P4 + Pico firmware (keeps SD card data){S.RESET}")
        print()
        print(f"      {S.GREEN}{S.BOLD}[2]{S.RESET}  {S.BOLD}🗄️  Full SD Card Deploy{S.RESET}")
        print(f"           {S.DIM}Erase & re-write SD card + flash all firmware{S.RESET}")
        print()
        separator()
        print()
        print(f"      {S.CYAN}{S.BOLD}[3]{S.RESET}  Flash ESP32-P4 only")
        print(f"      {S.CYAN}{S.BOLD}[4]{S.RESET}  Flash RP2350 (Pico) only")
        print(f"      {S.CYAN}{S.BOLD}[5]{S.RESET}  Deploy SD card image only")
        print()
        print(f"      {S.RED}{S.BOLD}[0]{S.RESET}  Exit\n")

        choice = ask("Select")

        if choice == "0":
            clear()
            print(f"\n  {S.CYAN}Goodbye 👋{S.RESET}\n")
            sys.exit(0)

        elif choice in ("1", "2"):
            clear()
            banner()
            header("Channel Selection")
            channel = select_channel()
            if choice == "1":
                wizard_quick(channel)
            else:
                wizard_full(channel)
            ask("\nPress Enter to return to menu")

        elif choice in ("3", "4", "5"):
            clear()
            banner()
            titles = {"3": "Flash ESP32-P4 Only",
                      "4": "Flash RP2350 (Pico) Only",
                      "5": "Deploy SD Card Image Only"}
            header(titles[choice])
            channel = select_channel()
            if choice == "3":
                flash_p4_only(channel)
            elif choice == "4":
                flash_pico_only(channel)
            else:
                deploy_sd_only(channel)
            ask("\nPress Enter to return to menu")

        else:
            status("Invalid choice", "err")
            time.sleep(0.5)


# ═══════════════════════════════════════════════════
#  CLI ARGUMENT SUPPORT
# ═══════════════════════════════════════════════════
def parse_args():
    p = argparse.ArgumentParser(
        description="dadamachines TBD-16 Firmware Update Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s                             Interactive wizard
  %(prog)s --quick                     Quick update (latest stable)
  %(prog)s --full                      Full SD card deploy (latest stable)
  %(prog)s --quick --channel beta      Quick update from beta channel
  %(prog)s --channel feature-test-xyz  Use a specific feature branch
  %(prog)s --p4-only                   Flash only ESP32-P4
  %(prog)s --pico-only                 Flash only RP2350 Pico
  %(prog)s --install-esptool           Install/upgrade esptool and exit
        """,
    )
    p.add_argument("--quick", action="store_true",
                   help="Run Quick Update wizard (P4 + Pico, no SD erase)")
    p.add_argument("--full", action="store_true",
                   help="Run Full SD Deploy wizard")
    p.add_argument("--channel", type=str, default="stable",
                   help="Firmware channel: stable, beta, staging, or feature-test-NAME (default: stable)")
    p.add_argument("--p4-only", action="store_true", dest="p4_only",
                   help="Flash only ESP32-P4 firmware")
    p.add_argument("--pico-only", action="store_true", dest="pico_only",
                   help="Flash only RP2350 Pico firmware")
    p.add_argument("--install-esptool", action="store_true", dest="install",
                   help="Install/upgrade esptool and exit")
    return p.parse_args()


def run_cli(args):
    """Handle CLI arguments."""
    if platform.system() == "Windows":
        os.system("")

    banner()

    if args.install:
        install_esptool()
        find_esptool()
        return

    channel = args.channel
    # "beta" is a shorthand — use staging (latest pre-release)
    if channel == "beta":
        channel = "staging"

    if args.quick:
        wizard_quick(channel, is_cli=True)
    elif args.full:
        wizard_full(channel, is_cli=True)
    elif args.p4_only:
        flash_p4_only(channel)
    elif args.pico_only:
        flash_pico_only(channel)


# ═══════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════
def main():
    args = parse_args()

    has_flags = any([
        args.quick, args.full, args.p4_only, args.pico_only, args.install
    ])

    if has_flags:
        run_cli(args)
    else:
        main_menu()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {S.CYAN}Interrupted — goodbye 👋{S.RESET}\n")
        sys.exit(0)
