from __future__ import annotations

import shutil
import subprocess
import sys
import re
from collections.abc import Callable
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 11)
MIN_NODE_MAJOR = 20


def validate_prerequisites(
    python_version: tuple[int, int] | None = None,
    npm_path: str | None = None,
    node_version: tuple[int, int, int] | None = None,
) -> tuple[bool, str]:
    version = python_version or (sys.version_info.major, sys.version_info.minor)
    if version < MIN_PYTHON:
        return False, "需要 Python 3.11 或更新版本。请安装较新的 Python，然后重新运行安装器。"
    if npm_path is None:
        npm_path = shutil.which("npm")
    if not npm_path:
        return False, "未找到 Node.js/npm。请安装 Node.js 20 或更新版本，然后重新运行安装器。"
    if node_version is None:
        node_path = shutil.which("node")
        if not node_path:
            return False, "未找到 Node.js。请安装 Node.js 20 或更新版本，然后重新运行安装器。"
        try:
            result = subprocess.run([node_path, "--version"], capture_output=True, text=True, check=False)
            match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
            if result.returncode != 0 or match is None:
                return False, "无法读取 Node.js 版本。请重新安装 Node.js 20 或更新版本。"
            node_version = tuple(map(int, match.groups()))
        except OSError:
            return False, "无法启动 Node.js。请重新安装 Node.js 20 或更新版本。"
    if node_version[0] < MIN_NODE_MAJOR:
        return False, f"需要 Node.js {MIN_NODE_MAJOR} 或更新版本。当前检测到 {node_version[0]}.{node_version[1]}.{node_version[2]}。"
    return True, "环境检查通过。"


def install_commands(
    python_executable: str | None = None,
    npm_executable: str = "npm",
    frontend_modules_exists: bool | None = None,
) -> list[list[str]]:
    python_executable = python_executable or sys.executable
    if frontend_modules_exists is None:
        frontend_modules_exists = (ROOT / "frontend" / "node_modules").is_dir()
    frontend_install = "install" if frontend_modules_exists else "ci"
    return [
        [python_executable, "-m", "pip", "install", "-e", "."],
        [npm_executable, frontend_install, "--prefix", "frontend"],
        [npm_executable, "run", "build", "--prefix", "frontend"],
    ]


def run_installer(
    *,
    python_executable: str | None = None,
    npm_executable: str | None = None,
    run: Callable = subprocess.run,
) -> int:
    npm = npm_executable or shutil.which("npm")
    ok, message = validate_prerequisites(npm_path=npm)
    if not ok:
        print(message, file=sys.stderr)
        return 1

    commands = install_commands(python_executable, npm)
    descriptions = ["安装 Python 后端", "安装锁定版本的前端依赖", "构建 Web 前端"]
    for description, command in zip(descriptions, commands, strict=True):
        print(f"\n{description}…", flush=True)
        try:
            result = run(command, cwd=ROOT, check=False)
        except OSError as error:
            print(f"无法执行安装命令：{error}", file=sys.stderr)
            return 1
        if result.returncode != 0:
            print("安装未完成。请根据上方错误信息修复环境后重新运行 install.py。", file=sys.stderr)
            return result.returncode or 1
    print("\n安装完成。运行 python run.py 启动本机工作台。")
    return 0


def main() -> int:
    return run_installer()


if __name__ == "__main__":
    raise SystemExit(main())
