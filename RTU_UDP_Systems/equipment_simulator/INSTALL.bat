@echo off
cd /d "%~dp0"
echo Installing dependencies...
pip install -r requirements.txt
echo Done.
pause
