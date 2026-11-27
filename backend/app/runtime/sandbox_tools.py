import os
import uuid

from backend.app.runtime.docker_sandbox import DockerSandboxUnavailable

MAX_GENERATED_CODE_CHARS = 64_000
MAX_CODE_ARGUMENT_BYTES = 64 * 1024


def sandbox_code_tool(existing_tools: list[dict], *, mode: str = 'sandbox') -> dict:
    existing_names = {tool['name'] for tool in existing_tools}
    prefix = 'sandbox' if mode == 'sandbox' else 'local'
    name = f'{prefix}:run_python'
    while name in existing_names:
        name = f'{prefix}:run_python:{uuid.uuid4().hex[:8]}'
    return {
        'name': name,
        'description': (
            'Run user-reviewed Python code in a temporary Docker sandbox. Never use for secrets or host filesystem access.'
            if mode == 'sandbox' else
            'Run Python in the selected local working directory after user approval. Define run(arguments) to return a JSON result.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'code': {'type': 'string', 'maxLength': MAX_GENERATED_CODE_CHARS},
                'arguments': {'type': 'object'},
            },
            'required': ['code', 'arguments'],
            'additionalProperties': False,
        },
        'risk': 'side_effect',
        'plugin_id': f'internal:{"docker-sandbox" if mode == "sandbox" else "local"}-code',
    }


def generated_code_approval(workspace: dict) -> dict:
    local = workspace['execution_mode'] == 'local'
    return {'execution_mode': workspace['execution_mode'], 'working_directory': workspace['path'] if local else None}


def _sandbox_execute(state):
    executor = getattr(state, 'sandbox_executor', None)
    execute = getattr(executor, 'execute_python_async', None)
    if not callable(execute):
        raise DockerSandboxUnavailable('已选择 Docker 沙箱，但沙箱执行器不可用；未回退到本机执行')
    return execute


async def execute_generated_code(state, workspace: dict, arguments: dict):
    if workspace['execution_mode'] == 'sandbox' or os.name != 'nt':
        execute = _sandbox_execute(state)
        return await execute(arguments['code'], arguments['arguments'])
    from backend.app.routes.workspaces import workspace_path
    return await state.local_code_executor.execute(arguments['code'], arguments['arguments'], cwd=workspace_path(workspace['path']))


async def execute_skill_script(state, workspace: dict, source: str, arguments: dict):
    if workspace['execution_mode'] == 'sandbox' or os.name != 'nt':
        execute = _sandbox_execute(state)
        return await execute(source, arguments)
    from backend.app.routes.workspaces import workspace_path
    return await state.local_code_executor.execute(
        source, arguments, cwd=workspace_path(workspace['path']),
    )
