@echo off
REM Installation script for PowerMate from source

echo Installing PowerMate...
echo.

REM Check Python version
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.11 ^(64-bit^) is not installed or not in PATH
    echo The pinned requirements have no wheels for Python 3.14.
    echo Please install Python from https://www.python.org/
    pause
    exit /b 1
)

echo Installing dependencies from requirements.txt...
pip install -r requirements.txt

if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    echo Check your internet connection and try again
    pause
    exit /b 1
)

echo.
echo Installation complete!
echo.
echo Next steps:
echo 1. Install the WinUSB driver via Zadig:
echo    - Download from https://zadig.akeo.ie/
echo    - Plug in PowerMate
echo    - In Zadig: Options ^> List All Devices ^> select Griffin PowerMate ^> WinUSB ^> Replace Driver
echo.
echo 2. Run the app:
echo    python main.py
echo.
pause
