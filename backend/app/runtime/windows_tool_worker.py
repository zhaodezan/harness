"""Fixed child entry point for trusted Python Tools executed by Windows PowerShell.

The parent sends source and arguments as JSON over stdin.  The source never appears
in the PowerShell command line; this process is constrained by the parent's job
object and output limits.
"""
import asyncio
import inspect
import json
import sys


WORKER_RESULT_PREFIX = 'HARNESS_TOOL_RESULT:'
def execute(payload):
    source = payload.get('source')
    arguments = payload.get('arguments')
    if not isinstance(source, str) or not isinstance(arguments, dict):
        raise ValueError('Python Tool 请求无效')

    scope = {'__name__': '__main__', '__file__': '<python-tool>'}
    exec(compile(source, '<python-tool>', 'exec'), scope)
    function_name = payload.get('function_name')
    if function_name is not None:
        if not isinstance(function_name, str) or not function_name.isidentifier():
            raise ValueError('Python Tool 函数名无效')
        entry = scope.get(function_name)
        if not callable(entry):
            raise ValueError('Python Tool 函数不存在')
        result = entry(**arguments)
    else:
        entry = scope.get('run')
        if not callable(entry):
            raise ValueError('Python Tool 必须定义 run(arguments)')
        result = entry(arguments)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return result


if __name__ == '__main__':
    payload = json.load(sys.stdin)
    if payload.get('directory'):
        import os
        os.chdir(payload['directory'])
    result_token = payload.get('_result_token')
    if not isinstance(result_token, str) or not result_token:
        raise ValueError('Python Tool 响应协议无效')
    print(f'{WORKER_RESULT_PREFIX}{result_token}:' + json.dumps(
        {'result': execute(payload)}, allow_nan=False,
    ))
