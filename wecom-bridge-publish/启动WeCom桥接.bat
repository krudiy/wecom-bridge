@echo off
setlocal
title WeCom Bridge Launcher
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Please install it from https://python.org
    pause
    exit /b 1
)

if not exist .venv (
    echo [INFO] Creating virtualenv .venv ...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

echo [INFO] Installing dependencies ...
pip install -q -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
)

if not exist config.yaml (
    echo [INFO] First run: created config.yaml from example. Fill in Bot ID/Secret before real use.
    copy /y config.yaml.example config.yaml >nul
)
if not exist agent-workspace mkdir agent-workspace

echo ============================================
echo   Starting WeCom Bridge ...
echo   Ctrl+C to stop.
echo ============================================
python bridge.py -c config.yaml
pause
endlocal
