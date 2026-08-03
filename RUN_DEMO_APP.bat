@echo off
setlocal
cd /d "%~dp0"

echo === RUN SCANNER ===
python scanner.py
if errorlevel 1 (
  echo.
  echo [ERROR] Scanner failed. Paper Trader will not run.
  pause
  exit /b 1
)

echo.
echo === RUN INTELLIGENCE RADARS ===
python run_intelligence.py
if errorlevel 1 (
  echo.
  echo [WARNING] Intelligence Radars failed. Continuing with safe base allocation.
)

echo.
echo === RUN PAPER TRADER ===
python paper_trader.py
if errorlevel 1 (
  echo.
  echo [ERROR] Paper Trader failed.
  pause
  exit /b 1
)

echo.
echo === START DASHBOARD ===
start "DEMO_APP" cmd /k "python app.py"

echo Waiting for dashboard...
timeout /t 8 /nobreak >nul
start "" "http://127.0.0.1:5000/"

endlocal
