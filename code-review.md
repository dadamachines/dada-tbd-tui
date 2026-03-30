# Code Review: dadamachines TBD-16 Firmware Update Tool

**Reviewed:** 2026-03-30
**Scope:** Full repository — `flash_tool.py` (2,372 lines), `flash.sh`, `flash.bat`, CI/CD, README

---

## Overall Assessment

A well-crafted, production-grade terminal wizard for flashing ESP32-P4 and RP2350 firmware onto the TBD-16 drum machine. The single-file design is appropriate for the distribution model (download-and-run). Safety mechanisms for destructive SD card operations are thorough and layered. UX is polished with clear guidance through hardware steps.

**Verdict:** Ship-quality. Issues below are improvements, not blockers.

---

## Strengths

### Safety & Defensive Programming
- **5-layer erase validation** (`_is_safe_to_erase`) — protected paths, root checks, macOS volume checks, removable-media verification, directory existence. This is exactly right for a tool that deletes user data on removable media.
- **Zip-slip protection** in `extract_sd_image` — path traversal entries are detected and skipped.
- **MSC recovery flow** (lines 1820-1911) — the tool never leaves the device in a bricked state. If SD card mounting fails after MSC flash, the user gets explicit recovery options including reflashing P4 to restore normal boot.
- **Partition table parsing** with fallback — reads the real table, falls back to a known address if that fails. Good embedded systems pragmatism.

### User Experience
- **Action boxes** for physical hardware steps — clear visual separation between "software is working" and "you need to do something with cables."
- **Recovery tips** on every failure — the tool never just says "failed" without telling the user what to try next.
- **Light/dark terminal detection** — avoids the common "invisible text on white terminal" issue.
- **Cached downloads** — repeat runs don't re-download firmware.

### Cross-Platform
- macOS, Linux, Windows are handled throughout with platform-specific paths for serial ports, volume detection, disk info, and ejection. The launcher scripts (`flash.sh`, `flash.bat`) handle Python discovery well.

---

## Issues

### High Priority

#### 1. Downloaded firmware is never verified against a hash
**Location:** `download_file()` (line 299), all callers
**Issue:** The tool downloads `hash_url` and writes it to the SD card, but never verifies downloaded firmware files (P4 `.bin`, Pico `.uf2`, SD `.zip`) against their expected hash. A corrupted download or MITM would result in flashing bad firmware.
**Recommendation:** After downloading each firmware file, fetch the corresponding hash and compare. The `hash_url` infrastructure already exists — extend it to cover P4 and Pico binaries.

#### 2. `select_port()` has unreachable code after `while True` loop
**Location:** lines 672-721
**Issue:** The `while True` loop at line 672 only handles the case where `len(ports) == 0` or `len(ports) == 1`. When there are 2+ ports, the code at line 702 (`status(f"Found {len(ports)} port(s):\n", "ok")`) is unreachable — it's outside the `while` block but still inside the function, so it would only run if the `while True` broke, which never happens.
```python
while True:                    # line 672
    ...
    if len(ports) == 1:        # line 696
        ...
        if ask(...) == "y":
            return ports[0]
    # Missing: no `else` for len(ports) > 1
    # Loop repeats, never reaches line 702

status(f"Found {len(ports)} port(s):\n", "ok")  # UNREACHABLE
```
**Impact:** Users with multiple serial devices (e.g., JTAG + USB-serial adapter) can never select between them — the tool re-scans in an infinite loop and only auto-selects if there's exactly one.
**Fix:** Move the multi-port selection logic inside the `while True` loop as an `else` branch.

#### 3. `deploy_sd_only()` with MSC mode leaves device in MSC boot without automatic recovery
**Location:** lines 2096-2219
**Issue:** When using MSC mode in `deploy_sd_only()`, the function warns the user (lines 2213-2217) that the device is stuck in MSC mode but doesn't offer the recovery flow that `wizard_full()` has. The user must manually navigate to menu option [3], which is a worse experience than the full wizard's built-in recovery.
**Recommendation:** After the warning, offer to flash P4 firmware inline (same pattern as the MSC recovery in `wizard_full`).

### Medium Priority

#### (done) 4. `urllib.request.urlretrieve` is deprecated
**Location:** line 313
**Issue:** `urlretrieve` has been informally deprecated since Python 3.x and may be removed in future versions. It also doesn't support timeouts on the download itself (only the initial connection via the global socket timeout).
**Recommendation:** Replace with `urllib.request.urlopen` + chunked read into a file. This also enables proper timeout control and more predictable progress reporting.

#### 5. Broad `except Exception` in flash operations
**Location:** lines 329, 534, 599, 786, 936, 984
**Issue:** Several exception handlers catch `Exception` broadly. Most are fine for user-facing "something went wrong" messages, but `_try_esptool` (line 533) catches `(FileNotFoundError, Exception)` which is redundant — `Exception` already covers `FileNotFoundError`. More importantly, bare `except Exception` in subprocess calls can swallow `KeyboardInterrupt` in Python < 3.12 when it's raised during a C-level call.
**Recommendation:** Catch `(subprocess.SubprocessError, OSError)` instead of `Exception` in subprocess-related code.

#### 6. No cache invalidation or size validation
**Location:** `download_file()` usage at lines 1666, 1086, 1786, etc.
**Issue:** Cached files are reused if `os.path.exists(path)` is true, but:
- A partial download (interrupted) would be cached as a valid file
- A 0-byte or tiny error page (e.g., CDN 404 returning HTML) would persist
- No TTL — old cached firmware versions persist forever in `/tmp`

**Recommendation:** At minimum, check that cached files are non-zero and larger than some threshold (e.g., 1 KB for firmware files). Ideally, compare against the hash file from CDN.

#### 7. `action_box` doesn't account for emoji/Unicode width
**Location:** lines 183-197
**Issue:** The padding calculation `w - 2 - len(line)` uses Python's `len()` which counts characters, not display width. Emoji like "👉" are 2 columns wide in most terminals but `len("👉") == 1`. This can cause right-border misalignment.
**Recommendation:** Use `unicodedata.east_asian_width()` or `wcwidth` to compute display width, or stick to ASCII in the box content.

#### 8. `os.system("cls"/"clear")` for screen clearing
**Location:** line 130
**Issue:** `os.system()` spawns a shell, which is slow and can be a minor security concern if `PATH` is manipulated. On Windows, the empty `os.system("")` call (lines 2227, 2326) is used to enable ANSI escape sequences — this is a known workaround but it's undocumented in the code.
**Recommendation:** Add a comment explaining the Windows `os.system("")` trick. Consider using ANSI escape `\033[2J\033[H` for clearing instead of spawning a shell.

### Low Priority

#### 9. `platform.system()` called repeatedly
**Location:** Throughout (30+ call sites)
**Issue:** `platform.system()` is called on every code path that branches on OS. While the function itself is cheap (returns a cached string internally in CPython), using a module-level constant would be cleaner.
**Recommendation:** Add `PLATFORM = platform.system()` near the top and reference it.

#### 10. `import re` inside function body
**Location:** line 396, inside `_discover_feature_branches()`
**Issue:** `re` is imported inside the function instead of at module level. This is fine for optional code but inconsistent — `urllib` is also imported inside functions, while `json`, `struct`, `zlib` are at the top.
**Recommendation:** Move `re` to the top-level imports for consistency. The delayed `urllib` imports are reasonable (not needed unless downloading), but `re` is stdlib and virtually free to import.

#### 11. `flash.sh` uses hardcoded bright ANSI colors
**Location:** `flash.sh` lines 19-23
**Issue:** `flash.sh` always uses bright colors (`\033[91m`, `\033[92m`, `\033[96m`) regardless of terminal background. The main Python tool detects light/dark backgrounds, but the launcher doesn't.
**Impact:** Minor — the launcher only prints a few lines before handing off to the Python tool.

#### 12. CI only validates syntax, not behavior
**Location:** `.github/workflows/release.yml` line 23
**Issue:** The only validation is `py_compile.compile('flash_tool.py', doraise=True)` — this checks syntax but not imports, logic, or regressions. There are no unit tests.
**Recommendation:** Add basic unit tests for the pure functions that don't need hardware: `parse_partition_table()`, `build_ota_data()`, `_is_safe_to_erase()`, `build_urls()`, `_detect_light_background()`. These are the most critical functions and are easily testable.

#### 13. Windows `wmic` is deprecated
**Location:** lines 1223-1238
**Issue:** `wmic` was deprecated in Windows 10 21H1 and removed in some Windows 11 builds. The `_get_volume_info()` function uses it for filesystem and size info on Windows.
**Recommendation:** Use PowerShell `Get-Volume` or `Get-WmiObject` as a fallback, or use `ctypes` with `GetVolumeInformation`.

#### 14. `_select_beta_channel` returns "staging" on any invalid input
**Location:** line 444
**Issue:** If the user types garbage, it silently falls back to "staging" with a warning. This is reasonable UX, but "staging" could be surprising if the user intended to cancel. No way to go back from the beta sub-menu to the main channel selection.
**Recommendation:** Add a `[0] Back` option in the beta sub-menu.

---

## Architecture Notes

### Single-File Design: Justified
At 2,372 lines, the file is approaching the point where splitting would help maintainability, but for this project the single-file design is the right call:
- Distribution is "download and run" — no module import headaches
- CI packages it as a single Python file + launchers
- The 11 internal sections are well-organized with clear headers

If the file grows beyond ~3,000 lines, consider extracting `partition.py` (partition table parsing + OTA data) and `sd.py` (SD card operations) as they have the cleanest boundaries.

### Error Handling Philosophy: Correct
The tool consistently follows "warn and offer retry" rather than "crash on error." This is the right approach for a hardware flashing tool where:
- The user may need to physically reconnect cables
- Transient USB/serial errors are common
- Partial success is meaningful (P4 flashed but Pico skipped is a valid outcome)

### Security Model: Appropriate
The tool runs locally, downloads from a known CDN (GitHub Pages), and writes to connected hardware. The main risk surface is the SD card erase path, which has robust protection. Adding firmware hash verification (issue #1) would close the remaining gap.

---

## Summary Table

| # | Issue | Severity | Effort |
|---|-------|----------|--------|
| 1 | No firmware hash verification | High | Medium |
| 2 | Multi-port selection unreachable | High | Low |
| 3 | `deploy_sd_only` MSC recovery gap | High | Low |
| 4 | Deprecated `urlretrieve` | Medium | Medium |
| 5 | Broad `except Exception` | Medium | Low |
| 6 | No cache validation | Medium | Low |
| 7 | Emoji width in action_box | Medium | Low |
| 8 | `os.system` for clear | Medium | Low |
| 9 | Repeated `platform.system()` | Low | Low |
| 10 | `import re` in function body | Low | Trivial |
| 11 | Launcher hardcoded colors | Low | Trivial |
| 12 | No unit tests in CI | Low | Medium |
| 13 | Windows `wmic` deprecated | Low | Low |
| 14 | No "back" in beta sub-menu | Low | Trivial |
