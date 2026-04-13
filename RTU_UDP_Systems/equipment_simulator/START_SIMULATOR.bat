@echo off
title Equipment Simulator
cd /d "%~dp0"
python equipment_simulator.py %*
if errorlevel 1 (
  echo.
  echo   Simulator stopped.
  pause
)
