@echo off
chcp 65001 > nul
title Model Maker Web v3 - Gemma 4 E4B - Stage 1/2/3
cd /d "%~dp0"

set "PYTHON=C:\Program Files\Python312\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo ============================================
echo   Model Maker Web v3  -  http://localhost:8083
echo   Gemma 4 E4B (Ollama) ^| 2-Pass Extraction
echo   Stage 1 -^> Stage 2 -^> Stage 3
echo ============================================
echo.

echo [1/4] Clearing pycache...
for /d /r "model_maker_web_v3" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"

echo [2/4] Installing dependencies...
"%PYTHON%" -m pip install fastapi "uvicorn[standard]" python-multipart openpyxl PyMuPDF ollama websockets -q

rem If the Ollama tray app auto-relaunches, disable "Start Ollama on startup" so this server owns the configured environment.
echo [*] Restarting Ollama with performance env vars (flash attention + q8_0 KV cache)...
taskkill /F /IM ollama.exe >nul 2>&1
timeout /t 1 /nobreak >nul
set "OLLAMA_FLASH_ATTENTION=1"
set "OLLAMA_KV_CACHE_TYPE=q8_0"
start "" /min ollama serve
timeout /t 2 /nobreak >nul
echo.

echo [3/4] Checking Ollama...
ollama list 2>nul
if errorlevel 1 echo   [WARNING] Ollama not found. Install from https://ollama.com and run: ollama pull gemma4:12b
echo.

echo [4/4] Starting server...
echo.
start "" http://localhost:8083

:LOOP
"%PYTHON%" -m uvicorn model_maker_web_v3.backend.main:app --host 0.0.0.0 --port 8083 --reload --reload-dir model_maker_web_v3/backend --reload-dir model_maker_web_v3/static
echo.
echo [%date% %time%] Server stopped. Restarting in 5s... (Ctrl+C to quit)
timeout /t 5 /nobreak >nul
goto LOOP
