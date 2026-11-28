"""Fixed worker. This file imports third-party code only inside the plugin container."""
from __future__ import annotations

import json
import os
import sys

MAX_INPUT = 128 * 1024
MAX_MESSAGES = 128
MAX_RESULT = 192 * 1024


class WorkerError(ValueError):
    pass


def _tool(registry, reference):
    cls = registry.get_tool_cls(reference['provider'], reference['tool'])
    if cls is None:
        raise WorkerError('incompatible')
    if cls.has_runtime_parameters() if hasattr(cls, 'has_runtime_parameters') else False:
        raise WorkerError('unsupported')
    return cls


def execute(request: dict, registry) -> list[dict]:
    if 'probe' in request:
        for reference in request['probe']:
            _tool(registry, reference)
        return []
    tool = _tool(registry, request)
    if hasattr(tool, 'from_credentials'):
        instance = tool.from_credentials(request['credentials'])
    else:
        from dify_plugin.core.runtime import Session
        from dify_plugin.entities.tool import ToolRuntime
        instance = tool(ToolRuntime(credentials=request['credentials'], user_id=None, session_id=None), Session.empty_session())
    messages = []
    size = 0
    try:
        for item in instance.invoke(request['arguments']):
            kind = getattr(item.type, 'value', item.type)
            if kind == 'text':
                message = {'type': 'text', 'text': item.message.text}
            elif kind == 'json':
                message = {'type': 'json', 'data': item.message.json_object}
            else:
                raise WorkerError('unsupported')
            size += len(json.dumps(message, ensure_ascii=True, allow_nan=False))
            if len(messages) >= MAX_MESSAGES or size > MAX_RESULT:
                raise WorkerError('too_large')
            messages.append(message)
    except WorkerError:
        raise
    except Exception as error:
        text = str(error).lower()
        if 'backwards invoke' in text or 'session is not initialized' in text:
            raise WorkerError('unsupported') from None
        raise WorkerError('execution') from None
    return messages


def main() -> None:
    # Retain a separate host-protocol pipe; discard plugin and SDK diagnostics.
    with os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8') as output, open(os.devnull, 'wb') as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
        os.dup2(sink.fileno(), sys.stderr.fileno())
        try:
            data = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(data) > MAX_INPUT:
                raise WorkerError('execution')
            request = json.loads(data)
            # Load the installed SDK before making package modules importable.
            from dify_plugin import DifyPluginEnv
            from dify_plugin.core.plugin_registration import PluginRegistration
            sys.path.insert(0, os.getcwd())
            try:
                registration = PluginRegistration(DifyPluginEnv())
            except Exception:
                raise WorkerError('incompatible') from None
            result = {'messages': execute(request, registration)}
        except WorkerError as error:
            result = {'error': str(error)}
        except Exception:
            result = {'error': 'execution'}
        output.write(json.dumps(result, ensure_ascii=True, allow_nan=False))
        output.flush()


if __name__ == '__main__':
    main()
