@echo off
chcp 65001 > nul
title UDP Test Server
cd /d "%~dp0..\pc_programs"

setlocal EnableDelayedExpansion
echo ============================================================
echo   UDP Test Server V1.0.0
echo ============================================================
echo.

set "SERVER_PORT=13132"
set "RTU_PORT=9100"
set /p "SERVER_PORT=UDP Listen Port [13132]: "
set /p "RTU_PORT=RTU Port [9100]: "

echo.
echo   Listen: !SERVER_PORT!  RTU: !RTU_PORT!
echo ============================================================
echo.
python udp_test_server.py --port !SERVER_PORT! --rtu-port !RTU_PORT!
pause
