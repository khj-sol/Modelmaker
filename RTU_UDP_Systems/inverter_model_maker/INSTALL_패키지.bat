@echo off
chcp 65001 > nul
title Install Dependencies
cd /d "%~dp0"

set "PYTHON=C:\Program Files\Python312\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo ============================================
echo   Installing all dependencies...
echo ============================================
echo.

"%PYTHON%" -m pip install -r requirements.txt
echo.
echo [DONE] All packages installed.
pause
