@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python for Windows not found.
    echo [ERROR] Install Python, then reopen the terminal and run this script again.
    exit /b 1
)

if exist "venv" (
    if exist "venv\bin\python" (
        echo [ERROR] Existing "venv" is not a Windows virtual environment.
        echo [ERROR] Delete or rename "venv", then run this script again to create a Windows venv.
        exit /b 1
    )
    echo [INFO] Existing Windows venv directory found.
) else (
    echo [INFO] Creating Windows virtual environment...
    %PYTHON_CMD% -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        exit /b %errorlevel%
    )
)

echo [INFO] Upgrading pip...
call "venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 exit /b %errorlevel%

echo [INFO] Installing GPU-enabled PyTorch for NVIDIA CUDA...
pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision torchaudio
if errorlevel 1 (
    echo [WARN] GPU PyTorch install failed. Falling back to the default PyTorch wheel.
    pip install torch torchvision torchaudio
    if errorlevel 1 exit /b %errorlevel%
)

echo [INFO] Installing application dependencies...
pip install fastapi uvicorn pymupdf pandas pillow "transformers>=4.57.0" accelerate openpyxl python-multipart sentencepiece protobuf safetensors
if errorlevel 1 exit /b %errorlevel%

echo.
echo [INFO] Windows environment is ready.
echo [INFO] Run run_model_maker.bat to start the server.
endlocal
