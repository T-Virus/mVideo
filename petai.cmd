@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%"

set "PYEXE="
if exist "%CD%\.venv\Scripts\python.exe" (
    set "PYEXE=%CD%\.venv\Scripts\python.exe"
) else if exist "%CD%\lib\python\python.exe" (
    set "PYEXE=%CD%\lib\python\python.exe"
) else (
    where python >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
    echo [错误] 未找到 Python，请先安装依赖：uv sync --frozen
    exit /b 1
)

"%PYEXE%" -m PetAI.cli %*
exit /b %errorlevel%
