@echo off
chcp 65001 > nul
title RTU Dashboard (Production)
cd /d "%~dp0.."
echo ============================================================
echo   RTU Dashboard - Production Server (Watchdog)
echo   Web: http://localhost:8080
echo   UDP: 0.0.0.0:13132
echo   Auto-restart on crash (5s delay)
echo   Press Ctrl+C twice to stop permanently
echo ============================================================
echo.

:LOOP
echo [%date% %time%] Starting dashboard...
python -m web_server_prod.main %*
echo.
echo [%date% %time%] Dashboard exited (code: %ERRORLEVEL%)
echo   Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto LOOP
