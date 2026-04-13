@echo off
chcp 65001 > nul
title Equipment Simulator
cd /d "%~dp0..\pc_programs"
python equipment_simulator.py
echo.
echo   Simulator stopped.
pause
