@echo off
setlocal
cd /d "%~dp0"
python install.py
if errorlevel 1 (
  echo.
  echo Installation failed. Fix the message above and run install.bat again.
  pause
  exit /b 1
)
echo.
echo Installation complete. Run: python run.py
pause
