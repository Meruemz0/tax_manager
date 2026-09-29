@echo off
setlocal
cd /d "%~dp0"
set "UV_CACHE_DIR=%CD%\.uv-cache"
set "PYTHONPATH=%CD%\src"

if not exist ".env" (
  echo Missing .env. Copy .env.example to .env and fill in the database settings.
  pause
  exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed or is not on PATH.
  pause
  exit /b 1
)

uv run --frozen --env-file .env python start_windows.py
if errorlevel 1 echo The app did not start. Check the message above.
pause
