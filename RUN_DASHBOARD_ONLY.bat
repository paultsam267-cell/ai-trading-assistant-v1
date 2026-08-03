@echo off
cd /d "%~dp0"
start "NEXUS AI Trading Assistant" cmd /k python app.py
timeout /t 5 >nul
start chrome http://127.0.0.1:5000/
