@echo off
REM ──────────────────────────────────────────────────
REM  dadamachines TBD-16 — Firmware Update Launcher
REM ──────────────────────────────────────────────────
REM  Finds or installs Python 3, then runs flash_tool.py.
REM
REM  Usage:
REM    flash.bat                 Interactive wizard
REM    flash.bat --quick         Quick update
REM    flash.bat --full          Full SD deploy
REM ──────────────────────────────────────────────────
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "TOOL=%SCRIPT_DIR%flash_tool.py"
set "PYTHON="

REM Try common Python commands
for %%P in (python3 python py) do (
    where %%P >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=*" %%V in ('%%P -c "import sys; print(sys.version_info.major, sys.version_info.minor)" 2^>nul') do (
            for /f "tokens=1,2" %%A in ("%%V") do (
                if "%%A"=="3" if %%B geq 8 (
                    set "PYTHON=%%P"
                    goto :found
                )
            )
        )
    )
)

REM Python not found — try Windows Store / winget
echo.
echo   [91mPython 3.8+ is required but not found.[0m
echo.
echo   Install options:
echo.
echo     1. Windows Store (easiest):
echo        Open Start menu, search "Python", install from Microsoft Store
echo.
echo     2. Official installer:
echo        https://www.python.org/downloads/
echo        [93mIMPORTANT: Check "Add Python to PATH" during install![0m
echo.
echo     3. Via winget:
echo        winget install Python.Python.3.12
echo.

where winget >nul 2>&1
if %errorlevel% equ 0 (
    set /p INSTALL_CHOICE="   Install Python via winget now? (y/n) [y]: "
    if /i "!INSTALL_CHOICE!"=="" set "INSTALL_CHOICE=y"
    if /i "!INSTALL_CHOICE!"=="y" (
        echo.
        echo   Installing Python via winget ...
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        echo.
        echo   [93mPlease close and reopen this terminal, then run flash.bat again.[0m
        echo.
        pause
        exit /b 0
    )
)

echo.
echo   [91mPlease install Python 3.8+ and try again.[0m
pause
exit /b 1

:found
echo   [92m√[0m Using Python: %PYTHON%

if not exist "%TOOL%" (
    echo   [91mflash_tool.py not found in %SCRIPT_DIR%[0m
    pause
    exit /b 1
)

REM Note: arguments containing &, |, >, < or ^ are not supported in batch.
REM Use the Python tool directly if you need special characters in arguments.
%PYTHON% "%TOOL%" %*
