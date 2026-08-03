@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
python run_intelligence.py
pause
endlocal
