@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%"

echo ============================================================
echo   Pet AI Content Factory - Launcher
echo ============================================================
echo.

rem Select Python interpreter: project venv first, then bundled, then PATH.
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
    pause
    exit /b 1
)
echo [ENV] Python: %PYEXE%

rem 1) Initialize database and config template, idempotent.
echo.
echo [1/2] Initialize database and config template...
"%PYEXE%" -m PetAI.cli init
if errorlevel 1 (
    echo [ERROR] Init failed.
    pause
    exit /b 1
)

rem 2) Show status and menu.
echo.
echo [2/2] Cost control center status:
"%PYEXE%" -m PetAI.cli status
echo.
echo ------------------------------------------------------------
echo   Available commands in this window:
echo     petai.cmd status           Show cost control center
echo     petai.cmd tts-status       Show local TTS capability and license
echo     petai.cmd test-deepseek    Test DeepSeek, needs .env key first
echo     petai.cmd productions      Show video production records
echo     webui.bat                  Start original MoneyPrinterTurbo WebUI
echo.
echo   First use: edit .env and set DEEPSEEK_API_KEY, then run test-deepseek
echo ------------------------------------------------------------
echo.
set /p "LAUNCH=Start original WebUI now? Y/N: "
if /i "%LAUNCH%"=="Y" (
    echo Starting WebUI...
    call "%~dp0webui.bat"
)
endlocal
