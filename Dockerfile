# MCP 控制台服务的 Linux 容器版（stdio）。
# 用途有二：非 Windows 的 agent 用户没有 exe；glama.ai 等目录的自动检查要求容器能启动并响应 introspection。
# GUI 主程序不在此列 —— 它依赖 Windows WebView2，见 build.bat。
#
# 用法：
#   docker build -t ai-handoff-kanban-mcp .
#   docker run -i --rm -v %USERPROFILE%\.ai-kanban:/data ai-handoff-kanban-mcp
# （-i 必须保留：MCP 走 stdio；/data 挂载让数据落在宿主机而不是容器里）
FROM python:3.13-slim

WORKDIR /app

# 按锁文件安装（实测闭包，与 CI/打包同一份）
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

# MCP 服务只需要 app/ 与入口脚本；不服务前端，web/ 不进镜像
COPY app/ app/

ENV KANBAN_DATA_DIR=/data
VOLUME /data

# 规范入口（= pyproject 的 console script 跑同一个 serve()）。
# ⚠️ exec 形式是 **JSON 数组**，JSON 里没有注释语法 —— 这一行尾上不能再跟 `# 说明`：
# 那些词会被当作**参数**塞进去，容器启动时报 `/bin/sh: 1: [python,: not found`，
# 而 stdout/stderr 全空（2026-10-31 实测：镜像能构建成功、CI 的容器冒烟却什么都没收到）。
# 注释只能像上面这样单独成行。
CMD ["python", "-m", "app.mcp_server"]
