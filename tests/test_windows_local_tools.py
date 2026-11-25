import asyncio
import hashlib
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.runtime.cube_sandbox import SandboxUnavailable
from model_test_utils import create_test_agent
from test_chat_options import Secrets, invoke, sse


SOURCE = '''def weather(city: str) -> str:
    """Return example data, not a live forecast."""
    return f"Example: {city}"
'''


def config(tmp_path, **kwargs):
    return create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), **kwargs)


def trust(client, tool, **overrides):
    body = {'confirmed': True, 'source_sha256': hashlib.sha256(tool['source'].encode()).hexdigest(), **overrides}
    return client.put(f"/api/tools/{tool['id']}/windows-trust", json=body)


@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows PowerShell')
def test_windows_default_and_persistent_sandbox_override(tmp_path):
    app = config(tmp_path)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        tool = client.post('/api/tools', json={'source': SOURCE}).json()
        assert tool['execution_mode'] == 'windows_local'
        path = f"/api/tools/{tool['id']}"
        modified = client.put(path, json={'source': SOURCE + '\n# revised\n'}).json()
        assert modified['execution_mode'] == 'windows_local'
        assert client.delete(path + '/windows-trust').json()['execution_mode'] == 'sandbox'
    with TestClient(config(tmp_path), client=('127.0.0.1', 50000)) as client:
        [reopened] = client.get('/api/tools').json()
        assert reopened['execution_mode'] == 'sandbox'
        restored = client.put(path, json={'source': SOURCE}).json()
        assert restored['execution_mode'] == 'sandbox'
        assert trust(client, restored, confirmed=False).status_code == 422
        assert trust(client, restored, source_sha256='0' * 64).status_code == 409
        assert trust(client, restored).json()['execution_mode'] == 'windows_local'
        complex_source = SOURCE.replace('return f"Example: {city}"', 'return city.upper()')
        complex_tool = client.put(path, json={'source': complex_source}).json()
        assert complex_tool['execution_mode'] == 'sandbox'
        assert not complex_tool['windows_local_available']
        assert trust(client, complex_tool).status_code == 422


@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows PowerShell')
def test_default_read_only_tool_runs_through_windows_powershell_in_chat(tmp_path):
    requests = []
    city = '北京 & "quotes" $notACommand'

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if len(requests) == 1:
            return invoke('weather', {'city': city})
        return sse({'content': 'done'})

    app = config(tmp_path, http_transport=httpx.MockTransport(provider))
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        tool = client.post('/api/tools', json={'source': SOURCE}).json()
        assert tool['execution_mode'] == 'windows_local'
        assert client.get('/api/sandbox').json()['configured'] is False
        agent = create_test_agent(client, {'name': 'Weather', 'selected_local_tools': [tool['id']]}).json()
        session = client.post('/api/sessions', json={'agent_id': agent['id']}).json()
        result = client.post(f"/api/sessions/{session['id']}/messages", json={'content': 'test'})
        assert 'event: done' in result.text and 'event: approval_required' not in result.text
    assert json.loads(requests[-1]['messages'][-1]['content']) == {'result': f'Example: {city}'}


@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows PowerShell')
def test_local_legacy_risk_claim_does_not_bypass_approval(tmp_path):
    source = '''TOOL = {"name":"legacy", "description":"Example", "risk":"read_only", "input_schema":{"type":"object"}}
def run(arguments):
    return arguments
'''
    app = config(tmp_path)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        tool = client.post('/api/tools', json={'source': source}).json()
        assert tool['risk'] == 'read_only'
        assert tool['windows_local_available'] is False
        assert trust(client, tool).status_code == 422
        assert client.get('/api/tools').json()[0]['execution_mode'] == 'sandbox'
        with pytest.raises(SandboxUnavailable):
            asyncio.run(app.state.tool_registry.execute('legacy', {}, approved=False))


def test_windows_trust_is_unavailable_without_windows_runtime(tmp_path):
    app = config(tmp_path)
    app.state.local_tool_manager.windows_executor.powershell = None
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        tool = client.post('/api/tools', json={'source': SOURCE}).json()
        assert not tool['windows_local_available']
        assert trust(client, tool).status_code == 422
        assert tool['execution_mode'] == 'sandbox'


@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows PowerShell')
def test_windows_tool_timeout():
    from backend.app.runtime.windows_tool import WindowsToolExecutor, WindowsToolError

    with pytest.raises(WindowsToolError, match='超时'):
        asyncio.run(WindowsToolExecutor(timeout=0.001).execute(SOURCE, {'city': 'Beijing'}, function_name='weather'))


@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows PowerShell')
def test_windows_tool_rejects_changed_source(tmp_path):
    from backend.app.runtime.windows_tool import WindowsToolError

    app = config(tmp_path)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        tool = client.post('/api/tools', json={'source': SOURCE}).json()
        assert trust(client, tool).status_code == 200
        path = app.state.local_tool_manager.tools_dir / (tool['id'] + '.py')
        path.write_text(SOURCE.replace('Example', 'Changed'), encoding='utf-8')
        with pytest.raises(WindowsToolError, match='源码已修改'):
            asyncio.run(app.state.tool_registry.execute('weather', {'city': 'Beijing'}, approved=False))


def test_windows_trust_is_disabled_for_remote_requests(tmp_path):
    app = config(tmp_path)
    with TestClient(app, client=('203.0.113.10', 50000)) as client:
        tool = client.post('/api/tools', json={'source': SOURCE}).json()
        assert trust(client, tool).status_code == 403
