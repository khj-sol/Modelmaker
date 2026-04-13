@echo off
chcp 65001 > nul

if not defined RTU_KEEP_OPEN (
    cmd /k "set RTU_KEEP_OPEN=1 && %~s0"
    exit /b
)

echo.
echo ============================================================
echo   CM4 Boot Partition Setup
echo ============================================================
echo.
echo   This script configures the CM4-ETH-RS485-BASE-B boot partition.
echo   Run INSTALL_RTU_DEV.bat instead - it includes boot setup (Step 6).
echo.
echo   Manual steps if needed:
echo     1. Enable UART overlays in /boot/firmware/config.txt
echo     2. Disable serial-getty on ttyAMA0/3/4/5
echo     3. Remove console=serial0 from cmdline.txt
echo.
pause
