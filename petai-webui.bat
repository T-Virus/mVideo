@echo off
setlocal
cd /d "%~dp0"
set "CURRENT_DIR=%CD%"
set "PYTHONPATH=%CURRENT_DIR%"

echo ============================================================
echo   Pet AI Content Factory - Dedicated WebUI
echo ============================================================
echo.

rem Select Python interpreter: project venv first, then bundled, then PATH.
set "PYEXE="
dir "%CURRENT_DIR%\.venv\Scripts\python.exe" >nul 2>nul
if not errorlevel 1 set "PYEXE=%CURRENT_DIR%\.venv\Scripts\python.exe"
if not defined PYEXE (
    dir "%CURRENT_DIR%\lib\python\python.exe" >nul 2>nul
    if not errorlevel 1 set "PYEXE=%CURRENT_DIR%\lib\python\python.exe"
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

echo [1/2] Initialize database and config...
"%PYEXE%" -m PetAI.cli init >nul 2>&1
echo [2/2] Start PetAI WebUI...

rem Dedicated port 8601 by default, no conflict with original WebUI on 8501.
if not defined PETAI_WEBUI_PORT set "PETAI_WEBUI_PORT=8601"
echo.
echo   URL: http://127.0.0.1:%PETAI_WEBUI_PORT%
echo   Close this window to stop the service.
echo.

"%PYEXE%" -m streamlit run .\PetAI\webui.py --server.port=%PETAI_WEBUI_PORT% --server.address=127.0.0.1 --browser.gatherUsageStats=False --client.toolbarMode=minimal --logger.hideWelcomeMessage=True --server.showEmailPrompt=False

endlocal
