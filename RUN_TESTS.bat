@echo off
cd /d "%~dp0"
chcp 65001 >nul
python -m py_compile app.py scanner.py paper_trader.py paper_risk.py risk_config.py archive_utils.py capital_management.py intelligence_radar.py run_intelligence.py pionex_bot_watchlist.py
if errorlevel 1 pause & exit /b 1
python -m unittest discover -s tests -v
pause
