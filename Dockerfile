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

CMD ["python", "-m", "app.mcp_server"]          # 规范入口（= pyproject 的 console script 跑同一个 serve()）
