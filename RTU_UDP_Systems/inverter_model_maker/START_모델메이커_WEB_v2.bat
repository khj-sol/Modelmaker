@echo off
chcp 65001 > nul
title Model Maker Web v2  —  Stage 1/2/3
cd /d "%~dp0"

set "PYTHON=C:\Program Files\Python312\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo ============================================
echo   Model Maker Web v2  —  http://localhost:8082
echo   Stage 1 → Stage 2 → Stage 3
echo ============================================
echo.

echo [1/3] Clearing pycache...
for /d /r "model_maker_web_v2" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"

echo [2/3] Installing dependencies...
"%PYTHON%" -m pip install fastapi "uvicorn[standard]" python-multipart openpyxl PyMuPDF anthropic websockets -q

echo [3/3] Starting server...
echo.
start "" http://localhost:8082

:LOOP
"%PYTHON%" -m uvicorn model_maker_web_v2.backend.main:app --host 0.0.0.0 --port 8082 --reload --reload-dir model_maker_web_v2/backend
echo.
echo [%date% %time%] Server stopped. Restarting in 5s... (Ctrl+C to quit)
timeout /t 5 /nobreak >nul
goto LOOP
