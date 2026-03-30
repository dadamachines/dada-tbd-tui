#!/usr/bin/env bash
# ──────────────────────────────────────────────────
#  dadamachines TBD-16 — Firmware Update Launcher
# ──────────────────────────────────────────────────
#  Finds or installs Python 3, then runs flash_tool.py.
#  Works on macOS and Linux (including fresh installs).
#
#  Usage:
#    ./flash.sh                 # Interactive wizard
#    ./flash.sh --quick         # Quick update
#    ./flash.sh --full          # Full SD deploy
# ──────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL="$SCRIPT_DIR/flash_tool.py"
MIN_VERSION=8  # minimum Python 3.x

RED='\033[91m'
GREEN='\033[92m'
CYAN='\033[96m'
BOLD='\033[1m'
RESET='\033[0m'

die() { echo -e "  ${RED}✘ $1${RESET}" >&2; exit 1; }
info() { echo -e "  ${CYAN}ℹ${RESET} $1"; }
ok() { echo -e "  ${GREEN}✔${RESET} $1"; }

# Check if a python command meets minimum version
check_python() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        return 1
    fi
    local ver
    ver=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null) || return 1
    local major
    major=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null) || return 1
    [[ "$major" == "3" && "$ver" -ge "$MIN_VERSION" ]]
}

# Find a working Python 3.8+
find_python() {
    # Try common names in order of preference
    for cmd in python3 python python3.13 python3.12 python3.11 python3.10 python3.9 python3.8; do
        if check_python "$cmd"; then
            echo "$cmd"
            return 0
        fi
    done
    return 1
}

install_python() {
    echo
    echo -e "  ${RED}${BOLD}Python 3.8+ is required but not found.${RESET}"
    echo

    case "$(uname -s)" in
        Darwin)
            info "macOS detected"
            echo
            # Check if Homebrew is available
            if command -v brew &>/dev/null; then
                info "Installing Python via Homebrew …"
                brew install python3
            else
                info "Install options:"
                echo "    1. Install Homebrew first (recommended):"
                echo "       /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
                echo "       brew install python3"
                echo
                echo "    2. Download from https://www.python.org/downloads/"
                echo
                die "Please install Python 3.8+ and try again"
            fi
            ;;
        Linux)
            info "Linux detected"
            echo
            if command -v apt &>/dev/null; then
                info "Installing via apt …"
                echo "    sudo apt update && sudo apt install -y python3 python3-venv"
                echo
                info "Run the command above, then try again."
            elif command -v dnf &>/dev/null; then
                info "Installing via dnf …"
                echo "    sudo dnf install -y python3"
            elif command -v pacman &>/dev/null; then
                info "Installing via pacman …"
                echo "    sudo pacman -S python"
            else
                info "Download from https://www.python.org/downloads/"
            fi
            die "Please install Python 3.8+ and try again"
            ;;
        *)
            die "Unsupported OS. Install Python 3.8+ from https://www.python.org/downloads/"
            ;;
    esac
}

# ── Main ──
PYTHON=$(find_python || true)

if [[ -z "$PYTHON" ]]; then
    install_python
    # Try again after install
    PYTHON=$(find_python || true)
    if [[ -z "$PYTHON" ]]; then
        die "Python installation failed. Please install manually from https://www.python.org/downloads/"
    fi
fi

ok "Using $($PYTHON --version 2>&1)"

if [[ ! -f "$TOOL" ]]; then
    die "flash_tool.py not found in $SCRIPT_DIR"
fi

exec "$PYTHON" "$TOOL" "$@"
