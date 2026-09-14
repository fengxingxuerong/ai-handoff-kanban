@echo off
rem AI 交接看板 - 双击启动脚本（源码运行模式）
chcp 65001 >nul
cd /d "%~dp0.."

rem 优先使用项目自带虚拟环境
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\desktop.py"
) else (
  python "scripts\desktop.py"
)

if errorlevel 1 (
  echo.
  echo 启动失败。请确认已安装依赖：
  echo     pip install -r requirements.txt
  echo 或直接运行 build.bat 打包成 exe 使用。
)
pause
