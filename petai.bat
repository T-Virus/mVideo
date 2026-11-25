@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%"

echo ============================================================
echo   Pet AI Content Factory - 一键启动
echo   基于 MoneyPrinterTurbo v1.3.10（原项目完整保留）
echo ============================================================
echo.

rem 选择 Python 解释器（与原 webui.bat 同一优先级）
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
    echo [错误] 未找到 Python。请先安装依赖：uv sync --frozen
    pause
    exit /b 1
)
echo [环境] Python: %PYEXE%

rem 1) 初始化数据库与配置（幂等，不覆盖已有配置/Key）
echo.
echo [1/2] 初始化数据库与配置模板...
"%PYEXE%" -m PetAI.cli init
if errorlevel 1 (
    echo [错误] 初始化失败。
    pause
    exit /b 1
)

rem 2) 显示状态与菜单
echo.
echo [2/2] 成本控制中心状态：
"%PYEXE%" -m PetAI.cli status
echo.
echo ------------------------------------------------------------
echo   可用命令（在本窗口输入）：
echo     petai.cmd status          查看成本控制中心
echo     petai.cmd tts-status      查看本地 TTS 能力与许可证
echo     petai.cmd test-deepseek   测试 DeepSeek 连接（需先填 .env）
echo     petai.cmd productions     查看视频生产记录
echo     webui.bat                 启动原 MoneyPrinterTurbo WebUI
echo.
echo   首次使用：编辑 .env 填入 DEEPSEEK_API_KEY，再运行 test-deepseek
echo ------------------------------------------------------------
echo.
set /p "LAUNCH=是否现在启动原 WebUI？(Y/N): "
if /i "%LAUNCH%"=="Y" (
    echo 正在启动 WebUI...
    call "%~dp0webui.bat"
)
endlocal
