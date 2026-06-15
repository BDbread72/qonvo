@echo off
REM Qonvo dedicated server (Minecraft .jar style: run once, auto-installs)
REM Requires Python 3.11+ installed (like Java for Minecraft).
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
set "PYBIN=%VENV%\Scripts\python.exe"

if not exist "%PYBIN%" (
  echo [setup] First-time install... downloading libraries, may take a minute.
  where python >nul 2>nul
  if errorlevel 1 (
    echo !! Python 3.11+ is required. Install from https://python.org then re-run.
    pause
    exit /b 1
  )
  python -m venv "%VENV%"
  "%PYBIN%" -m pip install -q --upgrade pip
  "%PYBIN%" -m pip install -q -r "%ROOT%requirements-server.txt"
  echo [setup] done
)

cd /d "%ROOT%src"
if "%~1"=="" (
  "%PYBIN%" -m server run
) else (
  "%PYBIN%" -m server %*
)
