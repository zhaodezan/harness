import asyncio
import hashlib
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.runtime.docker_sandbox import DockerSandboxUnavailable
from backend.app.runtime.windows_tool import use_tool_workspace
from backend.app.tool_plugins import LocalToolManager
from backend.app.tools import ApprovalRequired, ToolRegistry
from model_test_utils import create_test_agent, create_test_agent_async
from test_chat_options import Secrets, invoke, sse


SOURCE = '''def weather(city: str) -> str:
    """Return example data, not a live forecast."""
    return f"Example: {city}"
'''

COMPLEX_SOURCE = '''def _normalize(value):
    return value.strip().upper()


def uppercase(value: str) -> str:
    """Convert a value to uppercase.

    Args:
        value: Text to convert.
    """
    return _normalize(value)
'''

LEGACY_SOURCE = '''TOOL = {"name": "legacy", "description": "Legacy tool", "input_schema": {
    "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]
}, "risk": "side_effect"}

def run(arguments):
    return arguments["value"] + "-legacy"
'''

LEGACY_READ_ONLY_SOURCE = '''TOOL = {"name": "legacy_lookup", "description": "Legacy lookup", "input_schema": {
    "type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]
}, "risk": "read_only"}

def run(arguments):
    return arguments["key"] + "-result"
'''


def config(tmp_path, **kwargs):
    return create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), **kwargs)


def test_windows_worker_executes_complete_normal_and_legacy_sources():
    from backend.app.runtime.windows_tool_worker import execute

    assert execute({
        'source': COMPLEX_SOURCE,
        'arguments': {'value': '  mixed  '},
        'function_name': 'uppercase',
    }) == 'MIXED'
    assert execute({
        'source': LEGACY_SOURCE,
        'arguments': {'value': 'ok'},
        'function_name': None,
    }) == 'ok-legacy'
    assert execute({
        'source': '''import asyncio\n\nasync def delayed(value: str) -> str:\n    """Return a delayed value."""\n    await asyncio.sleep(0)\n    return value + "-async"\n''',
        'arguments': {'value': 'ok'},
        'function_name': 'delayed',
    }) == 'ok-async'


def test_windows_worker_result_parser_finds_tokenized_envelope_amid_extra_output():
    from backend.app.runtime.windows_tool import _parse_worker_output

    token = 'token-for-test'
    stdout = (
        b'tool print before\n'
        + f'HARNESS_TOOL_RESULT:{token}:{{"result":"ok"}}\n'.encode()
        + b'atexit output after\n'
    )

    assert _parse_worker_output(stdout, token) == 'ok'


def test_windows_worker_result_parser_accepts_legacy_local_worker_envelope_with_noise():
    from backend.app.runtime.windows_tool import _parse_worker_output

    stdout = b'tool print before\n{"result":"ok"}\natexit output after\n'

    assert _parse_worker_output(stdout, 'unused-token', allow_legacy=True) == 'ok'


def test_use_tool_workspace_sets_and_restores_execution_mode(tmp_path):
    from backend.app.runtime.windows_tool import tool_execution_mode, tool_workspace

    assert tool_workspace.get() is None
    assert tool_execution_mode.get() is None
    with use_tool_workspace(str(tmp_path), execution_mode='sandbox'):
        assert tool_workspace.get() == str(tmp_path)
        assert tool_execution_mode.get() == 'sandbox'
    assert tool_workspace.get() is None
    assert tool_execution_mode.get() is None


def test_explicit_local_fails_closed_when_windows_executor_is_unavailable(tmp_path, monkeypatch):
    import backend.app.tool_plugins as tool_plugins
    from backend.app.runtime.windows_tool import WindowsToolError

    monkeypatch.setattr(tool_plugins.sys, 'platform', 'win32')

    class UnavailableWindowsExecutor:
        available = False

    sandbox_calls = []

    def sandbox_executor(*args, **kwargs):
        sandbox_calls.append((args, kwargs))
        return 'sandbox-result'

    registry = ToolRegistry()
    manager = LocalToolManager(
        tmp_path / 'tools', registry,
        sandbox_executor=sandbox_executor,
        windows_executor=UnavailableWindowsExecutor(),
    )
    manager.install_source(SOURCE)

    with use_tool_workspace(str(tmp_path), execution_mode='local'), pytest.raises(
        WindowsToolError, match='PowerShell.*不可用',
    ):
        asyncio.run(registry.execute('weather', {'city': 'x'}, approved=True))
    assert sandbox_calls == []


def test_explicit_sandbox_fails_closed_without_invoking_windows_executor(tmp_path):
    class FakeWindowsExecutor:
        available = True

        async def execute(self, *args, **kwargs):
            raise AssertionError('PowerShell must not run for an explicit sandbox')

    registry = ToolRegistry()
    manager = LocalToolManager(
        tmp_path / 'tools', registry, windows_executor=FakeWindowsExecutor(), sandbox_executor=None,
    )
    manager.install_source(COMPLEX_SOURCE)

    with use_tool_workspace(str(tmp_path), execution_mode='sandbox'), pytest.raises(DockerSandboxUnavailable):
        asyncio.run(registry.execute('uppercase', {'value': 'x'}, approved=True))


def test_explicit_sandbox_rejects_changed_installed_source(tmp_path):
    from backend.app.runtime.windows_tool import WindowsToolError

    sandbox_calls = []

    async def sandbox_executor(*args, **kwargs):
        sandbox_calls.append((args, kwargs))
        return 'sandbox-result'

    registry = ToolRegistry()
    manager = LocalToolManager(
        tmp_path / 'tools', registry, sandbox_executor=sandbox_executor,
    )
    item = manager.install_source(SOURCE)
    (manager.tools_dir / f"{item['id']}.py").write_text(SOURCE.replace('Example', 'Changed'), encoding='utf-8')

    with use_tool_workspace(str(tmp_path), execution_mode='sandbox'), pytest.raises(
        WindowsToolError, match='源码已修改',
    ):
        asyncio.run(registry.execute('weather', {'city': 'x'}, approved=True))
    assert sandbox_calls == []



@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows PowerShell')
def test_complex_and_legacy_tools_report_windows_local(tmp_path):
    app = config(tmp_path)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        complex_tool = client.post('/api/tools', json={'source': COMPLEX_SOURCE}).json()
        legacy_tool = client.post('/api/tools', json={'source': LEGACY_SOURCE}).json()

    assert complex_tool['execution_mode'] == 'windows_local'
    assert complex_tool['windows_local_available'] is True
    assert legacy_tool['execution_mode'] == 'windows_local'
    assert legacy_tool['windows_local_available'] is True


def test_obsolete_trust_endpoints_do_not_change_execution_or_create_markers(tmp_path):
    app = config(tmp_path)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        tool = client.post('/api/tools', json={'source': SOURCE}).json()
        assert trust(client, tool).status_code == 410
        assert client.delete(f"/api/tools/{tool['id']}/windows-trust").status_code == 410
        assert client.get('/api/tools').json()[0]['execution_mode'] == tool['execution_mode']
        assert not (app.state.local_tool_manager.tools_dir / f"{tool['id']}.windows-sandbox").exists()


def trust(client, tool, **overrides):
    body = {'confirmed': True, 'source_sha256': hashlib.sha256(tool['source'].encode()).hexdigest(), **overrides}
    return client.put(f"/api/tools/{tool['id']}/windows-trust", json=body)


@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows PowerShell')
def test_legacy_read_only_claim_respects_chat_approval_modes(tmp_path):
    async def run(mode):
        calls = []
        model_calls = []

        async def provider(request):
            body = json.loads(await request.aread())
            model_calls.append(body)
            if len(model_calls) == 1:
                return invoke('legacy_lookup', {'key': 'x'})
            return sse({'content': 'done'})

        app = config(tmp_path / mode, http_transport=httpx.MockTransport(provider))
        async def sandbox_execute(source, arguments, **kwargs):
            calls.append((source, arguments))
            return 'x-result'
        app.state.sandbox_executor.execute_python_async = sandbox_execute
        app.state.sandbox_executor.is_configured = lambda: True
        app.state.local_tool_manager.sandbox_executor = sandbox_execute
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as client:
                tool_response = await client.post('/api/tools', json={
                    'source': LEGACY_READ_ONLY_SOURCE,
                })
                assert tool_response.status_code == 201, tool_response.text
                tool = tool_response.json()
                agent_response = await create_test_agent_async(client, app, {
                    'name': 'Legacy Approval', 'selected_local_tools': [tool['id']],
                })
                assert agent_response.status_code == 201, agent_response.text
                agent = agent_response.json()
                session_response = await client.post('/api/sessions', json={
                    'agent_id': agent['id'],
                    'workspace': {'execution_mode': 'sandbox'},
                })
                assert session_response.status_code == 201, session_response.text
                session = session_response.json()
                if mode == 'user_approval':
                    task = asyncio.create_task(client.post(
                        f"/api/sessions/{session['id']}/messages",
                        json={'content': 'lookup', 'approval_mode': mode},
                    ))
                    for _ in range(200):
                        if app.state.pending_approvals:
                            break
                        await asyncio.sleep(0.01)
                    assert app.state.pending_approvals
                    assert calls == []
                    approval_id = next(iter(app.state.pending_approvals))
                    await client.post(f'/api/approvals/{approval_id}', json={'approved': True})
                    response = await task
                else:
                    response = await client.post(
                        f"/api/sessions/{session['id']}/messages",
                        json={'content': 'lookup', 'approval_mode': mode},
                    )
        assert response.status_code == 200
        assert 'event: done' in response.text
        assert ('event: approval_required' in response.text) is (mode == 'user_approval')
        assert calls == [(LEGACY_READ_ONLY_SOURCE, {'key': 'x'})]

    asyncio.run(run('user_approval'))
    asyncio.run(run('allow_all'))

@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows PowerShell')
def test_historical_markers_do_not_change_execution_and_are_cleaned_up(tmp_path):
    app = config(tmp_path)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        tool = client.post('/api/tools', json={'source': SOURCE}).json()
        path = f"/api/tools/{tool['id']}"
        marker = app.state.local_tool_manager.tools_dir / f"{tool['id']}.windows-sandbox"
        trust_marker = app.state.local_tool_manager.tools_dir / f"{tool['id']}.windows-trust"
        marker.touch()
        trust_marker.touch()
        assert client.put(path, json={'source': SOURCE + '\n# revised\n'}).json()['execution_mode'] == 'windows_local'
    with TestClient(config(tmp_path), client=('127.0.0.1', 50000)) as client:
        [reopened] = client.get('/api/tools').json()
        assert reopened['execution_mode'] == 'windows_local'
        complex_source = SOURCE.replace('return f"Example: {city}"', 'return city.upper()')
        complex_tool = client.put(path, json={'source': complex_source}).json()
        assert complex_tool['execution_mode'] == 'windows_local'
        assert complex_tool['windows_local_available']
        assert client.delete(path).status_code == 204
        assert not marker.exists() and not trust_marker.exists()


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
def test_local_legacy_tool_defaults_to_windows_local(tmp_path):
    app = config(tmp_path)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        tool = client.post('/api/tools', json={'source': LEGACY_SOURCE}).json()
        assert tool['risk'] == 'side_effect'
        assert tool['windows_local_available'] is True
        assert tool['execution_mode'] == 'windows_local'


def test_windows_trust_is_unavailable_without_windows_runtime(tmp_path):
    app = config(tmp_path)
    app.state.local_tool_manager.windows_executor.powershell = None
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        tool = client.post('/api/tools', json={'source': SOURCE}).json()
        assert not tool['windows_local_available']
        assert trust(client, tool).status_code == 410
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
        path = app.state.local_tool_manager.tools_dir / (tool['id'] + '.py')
        path.write_text(SOURCE.replace('Example', 'Changed'), encoding='utf-8')
        with pytest.raises(WindowsToolError, match='源码已修改'):
            asyncio.run(app.state.tool_registry.execute('weather', {'city': 'Beijing'}, approved=False))


def test_windows_trust_is_disabled_for_remote_requests(tmp_path):
    app = config(tmp_path)
    with TestClient(app, client=('203.0.113.10', 50000)) as client:
        tool = client.post('/api/tools', json={'source': SOURCE}).json()
        assert trust(client, tool).status_code == 410
