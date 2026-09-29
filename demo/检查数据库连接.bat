@echo off
setlocal
cd /d "%~dp0"
set "UV_CACHE_DIR=%CD%\.uv-cache"

if not exist ".env" (
  echo Missing .env. Configure the database connection first.
  pause
  exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed or is not on PATH.
  pause
  exit /b 1
)

uv run --frozen --env-file .env python check_database.py
pause
