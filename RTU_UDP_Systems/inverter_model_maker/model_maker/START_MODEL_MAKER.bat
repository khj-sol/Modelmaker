@echo off
chcp 65001 > nul
title RTU UDP Model Maker
cd /d "%~dp0"

echo ============================================================
echo  RTU UDP Model Maker - Modbus Register Code Generator
echo ============================================================
echo.

python --version 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

echo  Starting Model Maker...
echo.
python modbus_to_udp_mapper.py

if errorlevel 1 (
    echo.
    echo [ERROR] Model Maker exited with an error.
    echo   Tip: pip install PyMuPDF pdfminer.six
    pause
)
