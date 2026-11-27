import json
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from model_test_utils import create_test_agent
from test_chat_options import Secrets, invoke, sse


def traces(response):
    return [json.loads(frame.split('\ndata: ', 1)[1]) for frame in response.text.split('\n\n') if frame.startswith('event: trace\n')]


@pytest.mark.parametrize('scope', ['workspace', 'published', 'debug'])
@pytest.mark.parametrize('fails', [False, True])
def test_chat_trace_reports_tool_and_terminal_status(tmp_path, scope, fails):
    async def provider(request):
        body = json.loads(await request.aread())
        if body['messages'][-1]['role'] != 'tool':
            return invoke('example', {'value': 'private-tool-argument'})
        if fails:
            return httpx.Response(400, json={'error': {'message': 'private-provider-detail'}})
        return sse({'content': 'done'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    manager = app.state.local_tool_manager
    calls = []
    tool_output = f'private-tool-result{chr(0x2028)}第二行{chr(0x2029)}第三行'

    async def execute(backend, source, arguments, *, function_name, **kwargs):
        calls.append((backend, arguments, function_name))
        if fails:
            raise RuntimeError('private-tool-error')
        return tool_output

    class FakeWindowsExecutor:
        available = True

        async def execute(self, source, arguments, *, function_name, cwd):
            return await execute('windows', source, arguments, function_name=function_name)

    async def sandbox(source, arguments, *, function_name):
        return await execute('sandbox', source, arguments, function_name=function_name)

    manager.windows_executor = FakeWindowsExecutor()
    manager.sandbox_executor = sandbox
    source = 'def example(value: str) -> str:\n    """Example."""\n    return value\n'
    with TestClient(app) as client:
        tool = client.post('/api/tools', json={'source': source}).json()
        agent = create_test_agent(client, {'name': 'Trace', 'selected_local_tools': [tool['id']]}).json()
        if scope == 'debug':
            response = client.post(f"/api/agents/{agent['id']}/debug", json={
                'config': {'model_config_id': agent['model_config_id'], 'selected_local_tools': [tool['id']]},
                'messages': [{'role': 'user', 'content': 'test'}],
            })
        else:
            session = client.post('/api/sessions', json={'agent_id': agent['id']} if scope == 'published' else {}).json()
            response = client.post(f"/api/sessions/{session['id']}/messages", json={
                'content': 'test', 'model_config_id': agent['model_config_id'], 'capabilities': {'local_tools': [tool['id']]},
            })
        assert response.status_code == 200
        assert calls == [('windows' if sys.platform == 'win32' else 'sandbox', {'value': 'private-tool-argument'}, 'example')]
        events = traces(response)
        types = [event.get('event_type') for event in events]
        assert types.index('run_started') < types.index('tool_started') < types.index('tool_completed')
        completed = next(event for event in events if event['event_type'] == 'tool_completed')
        assert completed['status'] == ('failed' if fails else 'completed')
        assert types[-1] == ('model_error' if fails else 'run_completed')
        details = completed['tool_details']
        assert details['tool_name'] == 'example'
        assert json.loads(details['input']['text']) == {'value': 'private-tool-argument'}
        assert details['input']['truncated'] is False
        if fails:
            assert '工具执行失败' in details['error']['text']
            assert 'output' not in details
        else:
            assert details['output'] == {'text': tool_output, 'truncated': False}
            # JavaScript treats these characters as line endings when extracting SSE data with a regex.
            assert chr(0x2028) not in response.text and chr(0x2029) not in response.text
            assert 'error' not in details
        assert 'private-tool-error' not in str(events)
        assert 'private-provider-detail' not in str(events)
        assert all('private-' not in event['summary'] for event in events)
        if scope == 'debug':
            assert client.get('/api/logs').json() == []
        else:
            history = client.get('/api/logs', params={'session_id': session['id']}).json()
            assert {'tool_started', 'tool_completed', types[-1]} <= {event['event_type'] for event in history}
            assert next(event for event in history if event['id'] == completed['id'])['event_type'] == 'tool_completed'
            assert 'private-' not in str(history)
            assert all('tool_details' not in event for event in history)
            assert {event['session_id'] for event in history} == {session['id']}
            other = client.post('/api/sessions', json={}).json()
            assert client.get('/api/logs', params={'session_id': other['id']}).json() == []


def test_tool_trace_previews_redact_credentials_and_bound_long_results():
    from backend.app.runtime.tool_trace import tool_trace_details

    details = tool_trace_details('example', {'nested': [{'API-Key': 'private-key', 'name': 'visible'}]}, {
        'result': {'accessToken': 'private-token', 'body': '长' * 5000},
    })
    assert json.loads(details['input']['text']) == {'nested': [{'API-Key': '[REDACTED]', 'name': 'visible'}]}
    assert details['output']['truncated'] is True
    assert len(details['output']['text']) == 4000
    assert 'private-' not in str(details)
    assert '[REDACTED]' in details['output']['text']


def test_tool_trace_previews_mask_credentials_in_tabular_results():
    from backend.app.runtime.tool_trace import tool_trace_details

    details = tool_trace_details('query', {'passphrase': 'private-input'}, {'result': {
        'columns': ['name', 'password', 'private_key'],
        'rows': [['visible', 'private-password', 'private-key']],
    }})
    assert json.loads(details['input']['text']) == {'passphrase': '[REDACTED]'}
    assert json.loads(details['output']['text'])['rows'] == [['visible', '[REDACTED]', '[REDACTED]']]


@pytest.mark.parametrize('published', [False, True])
def test_saved_replies_link_to_their_own_run(tmp_path, published):
    async def provider(request):
        return sse({'content': 'answer'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Trace'}).json()
        session_id = client.post('/api/sessions', json={'agent_id': agent['id']} if published else {}).json()['id']
        path = f'/api/sessions/{session_id}'
        run_ids = []
        for text in ('first', 'second'):
            reply = client.post(path + '/messages', json={'content': text, 'model_config_id': agent['model_config_id']})
            run_ids.append(traces(reply)[-1]['run_id'])
        assert len(set(run_ids)) == 2
        with database_connection(app.state.database_path) as connection:
            connection.execute("INSERT INTO messages(id, session_id, role, content, metadata_json) VALUES ('legacy', ?, 'assistant', 'legacy', 'invalid json')", (session_id,))
        first, first_reply, second, second_reply, legacy = client.get(path + '/messages').json()
        assert [first_reply['run_id'], second_reply['run_id']] == run_ids
        assert all('run_id' not in item for item in (first, second, legacy))
        assert {item['run_id'] for item in client.get('/api/logs', params={'session_id': session_id}).json()} == set(run_ids)
        branch = client.post(path + '/branch', json={'message_id': second['id'], 'content': 'new question'}).json()['id']
        assert all('run_id' not in item for item in client.get(f'/api/sessions/{branch}/messages').json())
        assert client.get('/api/logs', params={'session_id': branch}).json() == []
