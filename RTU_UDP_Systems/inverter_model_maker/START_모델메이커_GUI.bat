@echo off
chcp 65001 > nul
title Model Maker GUI v1.3.0
cd /d "%~dp0"

set "PYTHON=C:\Program Files\Python312\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo [INFO] Checking dependencies...
"%PYTHON%" -m pip install PyMuPDF openpyxl anthropic -q

echo [INFO] Starting Model Maker GUI...
"%PYTHON%" -m model_maker.modbus_to_udp_mapper
if errorlevel 1 (
    echo.
    echo [ERROR] Model Maker exited with an error.
    pause
)
