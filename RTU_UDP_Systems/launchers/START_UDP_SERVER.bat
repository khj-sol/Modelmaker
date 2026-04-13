@echo off
chcp 65001 > nul
title RTU Production UDP Server
cd /d "%~dp0.."
echo ============================================================
echo   RTU Production UDP Server V1.0.0 (Watchdog)
echo   UDP: 0.0.0.0:13132
echo   Auto-restart on crash (5s delay)
echo   Press Ctrl+C twice to stop permanently
echo ============================================================
echo.

:LOOP
echo [%date% %time%] Starting UDP server...
python pc_programs/udp_server.py %*
echo.
echo [%date% %time%] Server exited (code: %ERRORLEVEL%)
echo   Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto LOOP
