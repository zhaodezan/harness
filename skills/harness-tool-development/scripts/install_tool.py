"""Install reviewed Python source and optionally enable it for one Harness Agent."""

import argparse
import ast
import hashlib
import http.client
import json
import re


DEFAULT_PORT = 8765
MAX_SOURCE_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
ID = re.compile(r'[0-9a-f]{32}')


def failure(code, message, **details):
    return {'ok': False, 'code': code, 'message': message, **details}


class ApiFailure(Exception):
    def __init__(self, code, message, **details):
        self.result = failure(code, message, **details)


def request(port, method, path, *, expected=200, payload=None):
    # Connect directly to loopback. No configured proxy or redirect can change the destination.
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        try:
            connection.connect()
        except OSError:
            raise ApiFailure('unreachable', '无法连接本机 Harness，请检查后端和端口。') from None
        try:
            body = None if payload is None else json.dumps(payload).encode('ascii')
            headers = {} if body is None else {'Content-Type': 'application/json'}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            if response.status != expected:
                if method != 'GET' and response.status >= 500:
                    raise ApiFailure('unknown_result', '写入结果未确认，请先检查 Tool 库和 Agent 配置，不要自动重试。')
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                detail = json.loads(raw) if len(raw) <= MAX_RESPONSE_BYTES else None
                message = detail.get('detail') if isinstance(detail, dict) else None
                raise ApiFailure('rejected', '本机 Harness 拒绝请求。', http_status=response.status,
                                 detail=message[:600] if isinstance(message, str) else None)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError('response limit exceeded')
            return json.loads(raw)
        except (OSError, http.client.HTTPException, ValueError):
            if method != 'GET':
                raise ApiFailure('unknown_result', '写入结果未确认，请先检查 Tool 库和 Agent 配置，不要自动重试。') from None
            raise ApiFailure('read_failed', '读不到本机 Harness 的有效响应，未继续操作。') from None
    finally:
        connection.close()


def tool_name(source):
    compile(source, '<reviewed-tool>', 'exec', dont_inherit=True)
    tree = ast.parse(source)
    public = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and not node.name.startswith('_')]
    legacy = any(isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'TOOL'
                 for target in node.targets) for node in tree.body)
    if len(public) != 1 or legacy:
        raise ValueError('请使用普通函数格式：一个顶层公开函数，不使用 TOOL 字典。')
    return public[0].name


def agent_settings(port, agent_id):
    agent = request(port, 'GET', f'/api/agents/{agent_id}')
    selected = agent.get('selected_local_tools') if isinstance(agent, dict) and agent.get('id') == agent_id else None
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        raise ApiFailure('invalid_agent', '无法确认指定 Agent 的工具配置。')
    return selected


def install(source, port, *, overwrite=None, agent_id=None):
    if agent_id is not None and not ID.fullmatch(agent_id):
        return failure('invalid_agent', '请提供明确的 Agent ID，不能猜测目标。')
    source = source.replace('\r\n', '\n').replace('\r', '\n')
    try:
        encoded = source.encode('utf-8')
        if not source.strip() or len(encoded) > MAX_SOURCE_BYTES:
            return failure('invalid_source', '请提供非空的 UTF-8 Python 源码，最多 256 KiB。')
        name = tool_name(source)
    except SyntaxError as exc:
        return failure('invalid_source', 'Python 语法错误，请修订并重新审阅。', line=exc.lineno)
    except UnicodeEncodeError:
        return failure('invalid_source', '工具源码必须是有效的 UTF-8 文本。')
    except (ValueError, RecursionError) as exc:
        return failure('invalid_source', str(exc))

    try:
        if agent_id is not None:
            agent_settings(port, agent_id)
        tools = request(port, 'GET', '/api/tools')
        if not isinstance(tools, list):
            return failure('read_failed', '无法确认 Tool 库，未安装。')
        matches = [tool for tool in tools if isinstance(tool, dict) and tool.get('name') == name]
        if matches:
            old = matches[0]
            identifier, old_source = old.get('id'), old.get('source')
            if len(matches) != 1 or not isinstance(identifier, str) or not ID.fullmatch(identifier) or not isinstance(old_source, str):
                return failure('ambiguous_conflict', '无法唯一确认同名 Tool，未覆盖。')
            # Bind confirmation to the original Tool, both source versions and the chosen Agent.
            confirmation = hashlib.sha256(json.dumps([identifier, old_source, source, agent_id]).encode('ascii')).hexdigest()
            if overwrite != confirmation:
                return failure('name_conflict', f'同名 Tool「{name}」已存在。请询问用户是否覆盖；已关联的 Agent 将使用新版本。',
                               tool_name=name, tool_id=identifier, overwrite_confirmation=confirmation)
            saved = request(port, 'PUT', f'/api/tools/{identifier}', payload={'source': source})
            action = 'updated'
        elif overwrite is not None:
            return failure('conflict_changed', '此前的同名 Tool 已不存在，请重新确认安装。未写入。')
        else:
            saved = request(port, 'POST', '/api/tools', expected=201, payload={'source': source})
            action = 'installed'
    except ApiFailure as exc:
        return exc.result

    tool_id = saved.get('id') if isinstance(saved, dict) and saved.get('name') == name else None
    if not isinstance(tool_id, str) or not ID.fullmatch(tool_id) or (action == 'updated' and tool_id != identifier):
        return failure('unknown_result', '未能确认 Tool 写入结果，请检查 Tool 库，不要自动重试。')
    result = {'ok': True, 'action': action, 'tool_id': tool_id, 'tool_name': name,
              'enabled': False, 'agent_id': agent_id, 'executed': False}
    if agent_id is not None:
        try:
            selected = agent_settings(port, agent_id)
            if tool_id not in selected:
                agent = request(port, 'PATCH', f'/api/agents/{agent_id}', payload={'selected_local_tools': [*selected, tool_id]})
                if not isinstance(agent, dict) or tool_id not in (agent.get('selected_local_tools') or []):
                    raise ApiFailure('unknown_result', '请在 Agent 配置里核对是否已启用。')
            result['enabled'] = True
        except ApiFailure as exc:
            result['enable_error'] = exc.result
            result['message'] = 'Tool 已保存，但未能确认是否已为指定 Agent 启用；请查看 Agent 配置。未执行工具。'
            return result
    result['message'] = 'Tool 已保存，已为指定 Agent 启用；未执行。' if result['enabled'] else 'Tool 已保存，未指定 Agent，未修改任何 Agent 配置；未执行。'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='Local Harness backend port (default: 8765).')
    parser.add_argument('--agent-id', help='Enable only for this explicitly identified Harness Agent.')
    parser.add_argument('--overwrite', help='Confirmation value from the conflict result, only after user approval.')
    parser.add_argument('--source-chunk', action='append', required=True,
                        help='A literal segment of the reviewed source. Repeat in order; joins without separators.')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    # The Skill CLI displays stdout only on exit 0; the JSON fields describe outcomes.
    print(json.dumps(install(''.join(args.source_chunk), args.port, overwrite=args.overwrite, agent_id=args.agent_id)))


if __name__ == '__main__':
    main()
