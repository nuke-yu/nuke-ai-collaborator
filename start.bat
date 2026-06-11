@echo off
REM Nuke AI Collaborator - Quick Start Script for Windows
REM Run this script from the project root directory

echo ============================================
echo   Nuke AI Collaborator - Starting...
echo ============================================
echo.

REM Check if Python is installed
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.11+ from https://www.python.org/
    echo IMPORTANT: Check "Add Python to PATH" during installation.
    echo.
    echo After installation, you may need to close and reopen this terminal.
    pause
    exit /b 1
)

REM Check if Node.js is installed
where node >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo.
    echo Please install Node.js 18+ from https://nodejs.org/
    echo.
    pause
    exit /b 1
)

echo [OK] Python and Node.js are installed.
echo.

REM Setup Backend
echo === Backend Setup ===
if not exist backend\requirements.txt (
    echo [ERROR] backend\requirements.txt not found.
    echo Please check that you're running this script from the project root directory.
    pause
    exit /b 1
)

if not exist backend\venv (
    echo Creating Python virtual environment...
    python -m venv backend\venv
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo [ERROR] Failed to create virtual environment.
        echo Try running this PowerShell window as Administrator.
        pause
        exit /b 1
    )
)

echo Activating virtual environment...
call backend\venv\Scripts\activate.bat

echo Installing backend dependencies...
echo (This may take a few minutes for the first run...)
pip install -r backend\requirements.txt

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Failed to install dependencies.
    echo.
    echo Common fixes:
    echo 1. Install Visual C++ Build Tools from:
    echo    https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo 2. Run PowerShell as Administrator
    echo 3. Try: pip install --user -r backend\requirements.txt
    pause
    exit /b 1
)

echo.
echo === Starting Application ===
echo.
echo Backend will run at: http://localhost:8000
echo Frontend setup (run in separate terminal):
echo   cd frontend
echo   npm install    (first time only)
echo   npm run dev
echo.
echo Press Ctrl+C to stop the backend.
echo ============================================
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

REM This will only be reached if backend is stopped
deactivate 2>nul
echo.
echo ============================================
echo Backend stopped.
pause
