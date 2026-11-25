from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from backend.app.launcher import find_available_port, validate_bind_host


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本机 Agent Harness")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    try:
        host = validate_bind_host(args.host)
        port = find_available_port(host, args.port)
    except (ValueError, OSError) as error:
        print(f"无法启动本机工作台：{error}", file=sys.stderr)
        return 1

    url = f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
    print(f"Agent Harness 正在启动：{url}")
    print("数据保存在当前用户的本地应用目录；按 Ctrl+C 停止。")

    if not args.no_browser:
        threading.Thread(
            target=lambda: (time.sleep(1), webbrowser.open(url)), daemon=True
        ).start()

    uvicorn.run("backend.app.main:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
