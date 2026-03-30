# Security Review: dada-tbd-tui

**Reviewed:** 2026-03-30
**Scope:** `flash_tool.py`, `flash.sh`, `flash.bat`, `.github/workflows/release.yml`

---

## Critical

### SEC-01: Infinite recursion in `_enable_windows_ansi()`
**Location:** flash_tool.py:147
**Issue:** Function calls itself instead of `os.system("")` — crashes with `RecursionError` on every Windows invocation.
**Fix:** Replace self-call with the actual Windows ANSI enable logic.

### SEC-02: No server-side hash verification for firmware
**Location:** flash_tool.py:342-438
**Issue:** The SHA-256 sidecar is self-referential — hash is computed locally from the downloaded file. A MITM or CDN compromise can substitute firmware undetected. The server-provided `hash_url` exists but is only written to the SD card, never used to verify the download.
**Fix:** Download the server hash and compare before flashing.

### SEC-03: Serial port path injection
**Location:** flash_tool.py:804-810
**Issue:** User-supplied port paths (when no ports auto-detected) are passed directly to subprocess without validation. While list-form subprocess prevents shell injection, paths are printed raw to terminal (ANSI escape injection) and passed to esptool unvalidated.
**Fix:** Validate against `^/dev/[a-zA-Z0-9._-]+$` (Unix) or `^COM\d+$` (Windows).

---

## High

### SEC-04: PowerShell injection via drive letter in `eject_sd_card()`
**Location:** flash_tool.py:1722-1731
**Issue:** `drive_letter` from user-supplied mount path is interpolated into a PowerShell `-Command` string. A crafted path can break out of the single-quoted string.
**Fix:** Validate drive letter is `[A-Z]:` before interpolation.

### SEC-05: PowerShell injection via drive letter in `_get_volume_info()`
**Location:** flash_tool.py:1345-1366
**Issue:** Same pattern — `drive_letter[0]` interpolated into PowerShell command string via user-supplied `vol_path`.
**Fix:** Same validation as SEC-04.

### SEC-06: pip install without version pinning (supply chain)
**Location:** flash_tool.py:722-723
**Issue:** `pip install --upgrade esptool` installs latest from PyPI with no pinned version or hash. A compromised PyPI package runs arbitrary code.
**Fix:** Pin version and use `--require-hashes`.

---

## Medium

### SEC-07: TOCTOU in `_is_safe_to_erase()` / `erase_sd_card()`
**Location:** flash_tool.py:1372-1563
**Issue:** Race window between safety check and actual erase — volume could be swapped at the same mount point.
**Fix:** Re-verify device node immediately before erasure.

### SEC-08: Predictable temp file paths
**Location:** flash_tool.py:884, flash_tool.py:1011
**Issue:** `tbd_partition_table.bin` and `ota_data_msc.bin` use fixed names in `/tmp` — symlink attack vector on multi-user systems.
**Fix:** Use `tempfile.mkstemp()`.

### SEC-09: World-readable cache directory
**Location:** flash_tool.py:59, flash_tool.py:325-327
**Issue:** Cache dir in `/tmp` created with default permissions. Other users can read/substitute firmware.
**Fix:** `os.makedirs(CACHE_DIR, exist_ok=True, mode=0o700)` or use `~/.cache/`.

### SEC-10: No explicit SSL context
**Location:** flash_tool.py:401, 464, 502
**Issue:** `urllib.request.urlopen()` relies on system CA store. No certificate pinning for CDN domain.
**Fix:** Explicitly create `ssl.create_default_context()`.

### SEC-11: `--channel` CLI arg allows arbitrary path components
**Location:** flash_tool.py:462
**Issue:** User-supplied channel name used directly in CDN URL construction. Value like `../../other-repo` could traverse GitHub Pages paths.
**Fix:** Validate against `^[a-z0-9-]+$`.

---

## Low

### SEC-12: User-supplied SD mount path accepted before safety check
**Location:** flash_tool.py:2031, 2055
**Issue:** Manual mount path is used in `_get_volume_info()` subprocess calls before `_is_safe_to_erase()` runs.
**Fix:** Run safety check immediately after accepting input.

### SEC-13: `flash.bat` passes unquoted `%*` arguments
**Location:** flash.bat:80
**Issue:** Special batch characters in arguments could be interpreted by cmd.exe.

### SEC-14: `flash.sh` auto-installs Python via brew without confirmation
**Location:** flash.sh:67
**Issue:** Runs `brew install python3` without asking. Violates explicit consent principle.

### SEC-15: Workflow-level `contents: write` permission
**Location:** release.yml:9
**Issue:** Broader than necessary — only the gh-release step needs write. Mitigated by SHA-pinned actions and `persist-credentials: false`.

---

## Informational

- **SEC-16:** No code signing or SLSA provenance for release archives
- **SEC-17:** `releases.json` parsed without schema validation
- **SEC-18:** No download resume (partial file deletion path is security-relevant)

---

## Positive Observations

- Zip-slip protection correctly implemented
- 5-layer erase safety with tests
- All subprocess calls use list-form (no `shell=True`)
- GitHub Actions pinned to commit SHAs
- `persist-credentials: false` on checkout
- HTTPS-only CDN hardcoded
- Failed downloads cleaned up

---

## Priority Summary

| Do Now | SEC-01 (recursion crash), SEC-02 (firmware hash), SEC-03 (port validation) |
|--------|---------------------------------------------------------------------------|
| **Do Soon** | SEC-04/05 (PowerShell injection), SEC-06 (pip pinning) |
| **Plan** | SEC-07 to SEC-11 |
| **Accept** | SEC-12 to SEC-18 |
