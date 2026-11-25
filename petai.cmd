@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%"

set "PYEXE="
dir "%CD%\.venv\Scripts\python.exe" >nul 2>nul
if not errorlevel 1 set "PYEXE=%CD%\.venv\Scripts\python.exe"
if not defined PYEXE (
    dir "%CD%\lib\python\python.exe" >nul 2>nul
    if not errorlevel 1 set "PYEXE=%CD%\lib\python\python.exe"
)
if not defined PYEXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
    echo [ERROR] Python not found. Run: uv sync --frozen
    exit /b 1
)

"%PYEXE%" -m PetAI.cli %*
exit /b %errorlevel%
