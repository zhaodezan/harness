"""Child process for one user-approved local Python invocation (not a sandbox)."""
import asyncio
import inspect
import json
import os
import sys


WORKER_RESULT_PREFIX = 'HARNESS_TOOL_RESULT:'


def main():
    payload = json.load(sys.stdin)
    os.chdir(payload['directory'])
    scope = {'__name__': '__main__', '__file__': '<generated-python>'}
    exec(compile(payload['source'], '<generated-python>', 'exec'), scope)
    entry = scope.get('run')
    result = entry(payload['arguments']) if callable(entry) else None
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    result_token = payload.get('_result_token')
    envelope = json.dumps({'result': result}, ensure_ascii=True, allow_nan=False)
    if isinstance(result_token, str) and result_token:
        print(f'{WORKER_RESULT_PREFIX}{result_token}:' + envelope)
    else:
        # Keep the original protocol for callers that invoke this worker directly.
        print(envelope)


if __name__ == '__main__':
    main()
