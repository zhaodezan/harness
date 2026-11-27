"""CLI execution for an explicitly selected, locally trusted Python Skill."""
import asyncio
import json
from pathlib import Path
import secrets

from backend.app.runtime.skill_cli_worker import ERROR_PREFIX
from backend.app.runtime.skill_context import resolve_skill_script
from backend.app.runtime.tool_trace import is_sensitive_field, redact_sensitive_fields
from backend.app.runtime.windows_tool import WindowsToolError, WindowsToolExecutor


MAX_CLI_ARGS = 64
MAX_CLI_ARG_CHARS = 4096
MAX_CLI_ARG_BYTES = 64 * 1024
CLI_TIMEOUT_SECONDS = 900
_ERROR_LABELS = {
    b'import': '缺少 Python 模块或模块导入失败；请检查 Skill 包文件和依赖',
    b'syntax': 'Python 脚本有语法错误',
    b'permission': 'Python 脚本访问文件或目录时权限不足',
    b'exception': 'Python 脚本运行时出错',
}


class SkillCLIExecutor(WindowsToolExecutor):
    worker_name = 'skill_cli_worker.py'

    def __init__(self, *, timeout: float = CLI_TIMEOUT_SECONDS):
        super().__init__(timeout=timeout)

    def _worker_environment(self) -> dict[str, str]:
        # Some local Skills locate their configuration through Path.home().
        # Do not forward arbitrary environment variables to the worker.
        return {'USERPROFILE': str(Path.home())}

    def _worker_result(self, stdout: bytes, stderr: bytes, returncode: int, result_token: str | None) -> str:
        if returncode == 0:
            return stdout.decode('utf-8', errors='replace').strip()
        marker = f'{ERROR_PREFIX}{result_token}:'.encode('ascii')
        category = next((bytes(line[len(marker):]) for line in stderr.splitlines() if line.startswith(marker)), b'')
        reason = _ERROR_LABELS.get(category, '请检查命令行参数或脚本运行环境')
        # Do not return raw stderr, failed stdout, or exception text; any may contain credentials.
        raise WindowsToolError(f'Skill CLI 执行失败（退出码 {returncode}）：{reason}')

    async def execute_script(self, script: Path, argv: list[str], *, cwd: Path) -> str:
        if not self.available:
            raise WindowsToolError('Skill CLI 仅支持 Windows 本机执行，当前不可用')
        if not isinstance(argv, list) or len(argv) > MAX_CLI_ARGS or any(
            not isinstance(arg, str) or '\x00' in arg or len(arg) > MAX_CLI_ARG_CHARS for arg in argv
        ):
            raise WindowsToolError('Skill CLI 参数无效或超过长度限制')
        if len(json.dumps(argv, ensure_ascii=True).encode('ascii')) > MAX_CLI_ARG_BYTES:
            raise WindowsToolError('Skill CLI 参数超过大小限制')
        token = secrets.token_hex(16)
        payload = json.dumps({'script': str(script), 'directory': str(cwd), 'argv': argv, '_result_token': token}).encode('ascii')
        try:
            async with asyncio.timeout(self.timeout):
                return await self._run(payload, token)
        except TimeoutError:
            raise WindowsToolError('Skill CLI 执行超时，进程已停止；如涉及外部请求，请先确认结果再重试') from None
        except WindowsToolError:
            raise
        except Exception:
            raise WindowsToolError('Skill CLI 未能启动，请检查本机执行环境') from None


def skill_cli_tool(existing_tools: list[dict]) -> dict:
    occupied = {tool['name'] for tool in existing_tools}
    base = 'skill:run_python_cli'
    name = base
    for index in range(len(occupied) + 1):
        if name not in occupied:
            break
        name = f'{base}:{index + 1}'
    return {
        'name': name,
        'description': 'Run a command-line Python script from a loaded Skill, with sibling imports and CLI flags. '
                       'Use this instead of skill:run_python for scripts documented as terminal commands. '
                       'Runs on the Windows host (not a sandbox), with the selected workspace as the current directory. '
                       'Pass arguments as individual argv items, not a shell command. Use --workspace . when required. '
                       'Do not put credentials in argv. Interactive input and stdin are not supported. '
                       'Returns stdout on success. If an external request may already '
                       'have been submitted, check its outcome before retrying.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'skill_name': {'type': 'string', 'maxLength': 128},
                'path': {'type': 'string', 'maxLength': 1024, 'description': 'Forward-slash relative path within the Skill.'},
                'argv': {'type': 'array', 'maxItems': MAX_CLI_ARGS, 'items': {'type': 'string', 'maxLength': MAX_CLI_ARG_CHARS}},
            },
            'required': ['skill_name', 'path', 'argv'], 'additionalProperties': False,
        },
        'risk': 'side_effect', 'plugin_id': 'internal:skill-python-cli',
    }


def skill_cli_display_arguments(arguments: dict) -> dict:
    """Hide values of common credential flags in UI previews, not in the actual call."""
    displayed = redact_sensitive_fields(arguments)
    argv = displayed.get('argv')
    if not isinstance(argv, list):
        return displayed
    hide_next = False
    for index, arg in enumerate(argv):
        hidden = hide_next
        if hidden:
            argv[index] = '[REDACTED]'
        hide_next = False
        if isinstance(arg, str) and arg.startswith('-'):
            flag, separator, _ = arg.partition('=')
            if is_sensitive_field(flag):
                if separator and not hidden:
                    argv[index] = f'{flag}=[REDACTED]'
                hide_next = not separator
    return displayed


def skill_cli_enabled(state, workspace: dict) -> bool:
    executor = getattr(state, 'skill_cli_executor', None)
    return workspace['execution_mode'] == 'local' and bool(executor and executor.available)


async def execute_skill_cli(state, workspace: dict, skill, arguments: dict) -> str:
    if not skill_cli_enabled(state, workspace):
        raise WindowsToolError('Skill CLI 只能在 Windows 本机模式运行，不会从 Docker 沙箱回退到本机')
    try:
        script = await asyncio.to_thread(resolve_skill_script, skill, arguments['path'])
        if not script.is_relative_to(state.plugin_store.skills_dir.resolve()):
            raise ValueError('script outside installed Skills')
    except (ValueError, OSError):
        raise WindowsToolError('请使用已加载 Skill 中实际存在的 .py 脚本相对路径') from None
    from backend.app.routes.workspaces import workspace_path

    return await state.skill_cli_executor.execute_script(script, arguments['argv'], cwd=workspace_path(workspace['path']))
