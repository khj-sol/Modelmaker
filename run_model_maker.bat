@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    if exist "venv\bin\python" (
        echo [ERROR] Invalid virtual environment detected in "venv".
        echo [ERROR] This launcher requires a Windows venv with "venv\Scripts\python.exe".
        echo [ERROR] Delete or rename "venv", then run setup_windows_env.bat.
    ) else (
        echo [ERROR] Windows virtual environment not found.
        echo [ERROR] Run setup_windows_env.bat first.
    )
    exit /b 1
)

if not exist "index.html" (
    echo [ERROR] Project files not found in current directory.
    exit /b 1
)

if not exist "app.py" (
    echo [ERROR] app.py not found.
    exit /b 1
)

echo [INFO] Starting Model Maker on Windows...
echo [INFO] Opening browser at http://127.0.0.1:8000 ...
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"
"venv\Scripts\python.exe" -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start Model Maker.
    exit /b %errorlevel%
)

endlocal
