@echo off
chcp 65001 > nul
title Model Maker Web v1
cd /d "%~dp0"

set "PYTHON=C:\Program Files\Python312\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo ============================================
echo   Model Maker Web v1  —  http://localhost:8181
echo ============================================
echo.

echo [1/2] Installing dependencies...
"%PYTHON%" -m pip install fastapi "uvicorn[standard]" python-multipart openpyxl PyMuPDF anthropic -q

echo [2/2] Starting server...
echo.
start "" http://localhost:8181

:LOOP
"%PYTHON%" -m model_maker_web.backend.main
echo.
echo [%date% %time%] Server stopped. Restarting in 5s... (Ctrl+C to quit)
timeout /t 5 /nobreak >nul
goto LOOP
