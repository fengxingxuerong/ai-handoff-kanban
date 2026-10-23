@echo off
rem AI 交接看板 - 一键打包脚本（打包成独立 exe）
chcp 65001 >nul
cd /d "%~dp0"

echo [1/6] 清理历史构建产物（只留 build\ 与 dist\，其余 build_* / dist_* 一律删）...
for /d %%D in ("%~dp0build_*") do rd /s /q "%%~D"
for /d %%D in ("%~dp0dist_*") do rd /s /q "%%~D"

echo [2/6] 创建虚拟环境...
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 (
    echo 创建虚拟环境失败，请确认已安装 Python 3.9+ 并加入 PATH
    pause
    exit /b 1
  )
)

echo [3/6] 安装依赖（首次较慢，请耐心等待）...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
".venv\Scripts\python.exe" -m pip install pyinstaller -q
if errorlevel 1 (
  echo 依赖安装失败，请检查网络后重试
  pause
  exit /b 1
)

echo [4/6] 生成构建戳（版本条显示的"打包时间"，源码态不会用到它）...
".venv\Scripts\python.exe" "%~dp0scripts\make_stamp.py"
if errorlevel 1 (
  echo 构建戳生成失败
  pause
  exit /b 1
)

echo [5/6] 生成 exe 版本资源（右键属性看得到版本与构建时间）...
".venv\Scripts\python.exe" "%~dp0scripts\make_versionfile.py"
if errorlevel 1 (
  echo 版本资源生成失败
  pause
  exit /b 1
)

echo [6/6] 打包中（两个产物：GUI 主程序 + MCP 控制台服务）...
rem 注意：--specpath 会把 spec 放进 build/，此时相对的 --add-data / --paths 会按 spec 所在目录解析，
rem 导致找不到 web 目录（报 "Unable to find ...\build\web"）。所以这里一律用 %~dp0 绝对路径。
".venv\Scripts\python.exe" -m PyInstaller -F -w -n "AI交接看板" ^
  "%~dp0scripts\desktop.py" ^
  --hidden-import app.main ^
  --hidden-import uvicorn ^
  --add-data "%~dp0web;web" ^
  --paths "%~dp0" ^
  --icon "%~dp0app.ico" ^
  --version-file "%~dp0build\version_info.txt" ^
  --distpath "%~dp0dist" ^
  --workpath "%~dp0build" ^
  --specpath "%~dp0build" ^
  --noconfirm
if errorlevel 1 (
  echo 打包失败，请把上方错误信息发给我排查
  pause
  exit /b 1
)

rem 第二个产物：MCP 服务。**故意不加 -w** —— MCP 走 stdio 传输，而 -w 的 GUI 子系统程序
rem 拿不到标准流（本项目最坑的一条历史教训：-w 打包后 sys.stdout / sys.stderr 是 None）。
rem 只装 exe 的用户没有 .venv，"让代理直接读写看板"这个最大卖点就对他不可用，
rem 所以这一份必须单独打出来，配置里直接指向它、不需要任何 Python 环境。
".venv\Scripts\python.exe" -m PyInstaller -F -n "AI交接看板-MCP" ^
  "%~dp0scripts\mcp_server.py" ^
  --hidden-import app.main ^
  --paths "%~dp0" ^
  --version-file "%~dp0build\version_info.txt" ^
  --distpath "%~dp0dist" ^
  --workpath "%~dp0build" ^
  --specpath "%~dp0build" ^
  --noconfirm
if errorlevel 1 (
  echo MCP 服务打包失败，请把上方错误信息发给我排查
  pause
  exit /b 1
)

echo.
echo ============================================
echo  打包完成！产物两个：
echo   dist\AI交接看板.exe      双击即用（数据在 %USERPROFILE%\.ai-kanban）
echo   dist\AI交接看板-MCP.exe  给 AI 代理读写看板用（stdio，无需 Python）
echo ============================================
pause
