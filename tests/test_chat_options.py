import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from backend.app.tools import ToolDefinition, model_tool_names
from model_test_utils import create_test_agent, create_test_model


class Secrets:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name)
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def sse(delta):
    return httpx.Response(200, text=f'data: {json.dumps({"choices": [{"delta": delta}]})}\n\ndata: [DONE]\n\n')


def invoke(tool, arguments, id='call'):
    return sse({'tool_calls': [{'index': 0, 'id': id, 'function': {'name': tool, 'arguments': json.dumps(arguments)}}]})


def test_chat_catalog_selection_is_optional_and_does_not_rewrite_agent(tmp_path):
    requests, executed = [], []

    async def provider(request):
        requests.append(json.loads(await request.aread()))
        return sse({'content': 'No tool needed'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        tool = client.post('/api/tools', json={'source': '''TOOL = {"name":"lookup", "description":"Lookup", "input_schema":{"type":"object"}, "risk":"read_only"}
def run(arguments): return "ok"
'''}).json()
        app.state.local_tool_manager.sandbox_executor = lambda *args: executed.append(args)
        agent = create_test_agent(client, {'name': 'Parent'}).json()
        child = create_test_agent(client, {'name': 'Child'}).json()
        session = client.post('/api/sessions', json={'agent_id': agent['id']}).json()
        catalog = client.get(f"/api/agents/{agent['id']}/chat-options")
        assert catalog.status_code == 200, catalog.text
        assert catalog.json()['options']['local_tools'] == [{'id': tool['id'], 'name': 'lookup'}]
        assert catalog.json()['options']['agents'] == [{'id': child['id'], 'name': 'Child'}]
        url = f"/api/sessions/{session['id']}/messages"
        selected = client.post(url, json={'content': 'one', 'capabilities': {'local_tools': [tool['id']], 'agents': [child['id']]}})
        assert 'event: done' in selected.text
        assert any(t['function']['name'] == 'lookup' for t in requests[0]['tools'])
        assert any('Child' in t['function']['description'] for t in requests[0]['tools'])
        assert executed == []
        assert client.get(f"/api/agents/{agent['id']}").json()['selected_local_tools'] == []
        client.patch(f"/api/agents/{agent['id']}", json={'selected_local_tools': [tool['id']]})
        assert 'event: done' in client.post(url, json={'content': 'two', 'capabilities': {}}).text
        assert not requests[1].get('tools')
        assert 'event: done' in client.post(url, json={'content': 'three'}).text
        assert requests[2]['tools'][0]['function']['name'] == 'lookup'
        assert client.post(url, json={'content': 'invalid', 'capabilities': {'agents': [agent['id']]}}).status_code == 422
        assert client.post(url, json={'content': 'invalid', 'capabilities': {'mcp_tools': ['mcp:unknown:search']}}).status_code == 422
        assert len(requests) == 3
        assert all(m['content'] != 'invalid' for m in client.get(url).json())


def test_model_override_uses_selected_connection_and_leaves_agent_unchanged(tmp_path):
    requests = []

    async def provider(request):
        requests.append((request.url.host, request.headers.get('Authorization'), json.loads(await request.aread())))
        return sse({'content': 'done'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Parent', 'model': 'default'}).json()
        chosen = create_test_model(client, base_url='https://alternate.test/v1', model='alternate', api_key='different')
        session = client.post('/api/sessions', json={'agent_id': agent['id']}).json()
        url = f"/api/sessions/{session['id']}/messages"
        assert 'event: done' in client.post(url, json={'content': 'hello', 'model_config_id': chosen}).text
        assert 'event: done' in client.post(url, json={'content': 'hello'}).text
        assert client.post(url, json={'content': 'unavailable', 'model_config_id': 'f' * 32}).status_code == 422
        assert client.get(f"/api/agents/{agent['id']}").json()['model_config_id'] == agent['model_config_id']
    assert [(host, key, body['model']) for host, key, body in requests] == [
        ('alternate.test', 'Bearer different', 'alternate'), ('example.test', 'Bearer test-key', 'default'),
    ]


def test_model_override_is_used_for_compaction_plan_execution_and_replanning(tmp_path):
    calls = []

    async def provider(request):
        body = json.loads(await request.aread())
        calls.append((request.url.host, request.headers.get('Authorization'), body['model']))
        if not body['stream']:
            text = {1: 'history summary', 2: '1. Try the lookup', 5: '1. Use a fallback'}[len(calls)]
            return httpx.Response(200, json={'choices': [{'message': {'content': text}}]})
        if len(calls) == 3:
            return invoke('lookup', {})
        usage = {'choices': [{'delta': {'content': 'done'}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
        return httpx.Response(200, text=f'data: {json.dumps(usage)}\n\ndata: [DONE]\n\n')

    def failed_lookup(_):
        raise RuntimeError('test failure')

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    app.state.tool_registry.register(ToolDefinition('lookup', 'Lookup', {'type': 'object'}, 'read_only', failed_lookup))
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Planner', 'mode': 'plan_execute', 'context_window': 1024,
                                          'input_rate_per_million': 1, 'output_rate_per_million': 2}).json()
        chosen = create_test_model(client, base_url='https://alternate.test/v1', model='alternate', api_key='different')
        session = client.post('/api/sessions', json={'agent_id': agent['id']}).json()
        with database_connection(app.state.database_path) as db:
            db.executemany("INSERT INTO messages(id,session_id,role,content) VALUES(?,?,'user',?)",
                           [(uuid4().hex, session['id'], 'history ' * 100) for _ in range(8)])
        response = client.post(f"/api/sessions/{session['id']}/messages", json={'content': 'hello', 'model_config_id': chosen})
        assert 'event: done' in response.text and 'context_compacted' in response.text and 'plan_replanning' in response.text
        result = next(e for e in client.get('/api/logs').json() if e['event_type'] == 'run_completed')
        assert result['input_tokens'] == 20 and result['estimated_cost'] is None
    assert calls == [('alternate.test', 'Bearer different', 'alternate')] * 6


def test_explicit_skill_instructions_are_loaded_only_on_model_request(tmp_path):
    root = tmp_path / 'data' / 'plugins' / 'skills'
    for name in ('guide', 'private'):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Use {name}\n---\n{name.upper()}_INSTRUCTIONS', encoding='utf-8')
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if len(requests) <= 2:
            loader = next(t['function']['name'] for t in body['tools'] if 'load_skill' in t['function']['name'])
            return invoke(loader, {'skill_name': 'private' if len(requests) == 1 else 'guide'}, str(len(requests)))
        return sse({'content': 'done'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Parent', 'selected_skills': ['private']}).json()
        session = client.post('/api/sessions', json={'agent_id': agent['id']}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={'content': 'hello', 'capabilities': {'skills': ['guide']}})
        assert 'event: done' in response.text, response.text
    assert 'Use guide' in json.dumps(requests[0])
    assert 'GUIDE_INSTRUCTIONS' not in json.dumps(requests[:2])
    assert 'GUIDE_INSTRUCTIONS' in requests[2]['messages'][-1]['content']
    assert 'PRIVATE_INSTRUCTIONS' not in json.dumps(requests)


def test_temporary_agent_delegation_is_not_inherited_by_child(tmp_path):
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if len(requests) == 1:
            return invoke(body['tools'][0]['function']['name'], {'agent_id': child['id'], 'task': 'Summarize this'})
        return sse({'content': 'child answer' if body['model'] == 'child' else 'done'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Parent', 'model': 'parent'}).json()
        child = create_test_agent(client, {'name': 'Child', 'model': 'child'}).json()
        other = create_test_agent(client, {'name': 'Other'}).json()
        session = client.post('/api/sessions', json={'agent_id': agent['id']}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={'content': 'hello', 'capabilities': {'agents': [child['id'], other['id']]}})
        assert '"status":"completed"' in response.text
        assert client.get(f"/api/agents/{agent['id']}").json()['selected_agents'] == []
    assert requests[1]['model'] == 'child'
    assert not requests[1].get('tools')
    assert 'child answer' in requests[2]['messages'][-1]['content']


@pytest.mark.parametrize('scope', ['workspace', 'published', 'debug'])
@pytest.mark.parametrize('scenario', ['selected', 'replaced', 'unselected'])
def test_mcp_skips_approval_without_bypassing_tool_selection(tmp_path, scope, scenario):
    requests, executions = [], []
    server_id = uuid4().hex
    tool_id = f'mcp:{server_id}:write'
    tool_name = 'Sample/write'
    alias = model_tool_names([tool_name])[tool_name]
    selected = [] if scenario == 'unselected' else [tool_id]

    def tool():
        return ToolDefinition(tool_name, 'Write sample record', {'type': 'object'},
                              'side_effect', lambda _: executions.append('write'), tool_id)

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if len(requests) == 1:
            assert (alias in [item['function']['name'] for item in body.get('tools', [])]) == bool(selected)
            if scenario == 'replaced':
                app.state.tool_registry.unregister(tool_name)
                app.state.tool_registry.register(tool())
            return invoke(alias, {})
        return sse({'content': 'done'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))

    async def run():
        async with app.router.lifespan_context(app):
            with database_connection(app.state.database_path) as db:
                db.execute("INSERT INTO mcp_servers(id,name,transport) VALUES(?,?,'sse')", (server_id, 'Sample'))
            app.state.mcp_manager.servers[server_id] = {'name': 'Sample'}
            app.state.tool_registry.register(tool())
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as client:
                model = create_test_model(app)
                agent = (await client.post('/api/agents', json={'name': 'Parent', 'model_config_id': model,
                           'selected_mcp_tools': [f'mcp:{server_id}:*']})).json()
                catalog = (await client.get(f"/api/agents/{agent['id']}/chat-options")).json()
                assert catalog['defaults']['mcp_tools'] == [tool_id]
                if scope == 'debug':
                    url = f"/api/agents/{agent['id']}/debug"
                    payload = {'config': {'model_config_id': model, 'selected_mcp_tools': selected},
                               'messages': [{'role': 'user', 'content': 'Try sample write'}]}
                else:
                    session = (await client.post('/api/sessions', json={'agent_id': agent['id']} if scope == 'published' else {})).json()
                    url = f"/api/sessions/{session['id']}/messages"
                    payload = {'content': 'Try sample write', 'model_config_id': model, 'capabilities': {'mcp_tools': selected}}
                task = asyncio.create_task(client.post(url, json=payload))
                try:
                    async with asyncio.timeout(5):
                        while not task.done():
                            assert not app.state.pending_approvals, 'MCP call must not wait for approval'
                            await asyncio.sleep(0.01)
                        response = await task
                    assert response.status_code == 200
                    assert 'event: done' in response.text and 'approval_required' not in response.text
                    if selected and scope != 'debug':
                        app.state.mcp_manager.servers.pop(server_id)
                        assert (await client.post(url, json=payload)).status_code == 422
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert executions == (['write'] if scenario == 'selected' else [])
    assert len(requests) == 2
    result = json.loads(requests[1]['messages'][-1]['content'])
    assert ('result' if scenario == 'selected' else 'error') in result
