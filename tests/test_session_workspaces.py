import asyncio
import json
import os
import re
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.routes.workspaces import WorkspaceChoice, debug_workspace
from backend.app.db import database_connection
from model_test_utils import create_test_model, create_test_agent
from test_chat_options import Secrets, invoke, sse


def test_session_remembers_group_and_reuses_its_default_folder(tmp_path):
    def app():
        application = create_app(tmp_path / 'test.sqlite3', data_dir=tmp_path / 'data')
        application.state.sandbox_executor.is_configured = lambda: True
        return application

    with TestClient(app()) as client:
        external = tmp_path / 'My files'
        external.mkdir()
        group = client.post('/api/workspaces', json={'path': str(external)}).json()
        assert group['name'] == external.name
        assert Path(group['path']) == external.resolve()
        named = client.post('/api/workspaces', json={'name': 'Analysis'}).json()
        duplicate = client.post('/api/workspaces', json={'name': 'Analysis'}).json()
        assert named['path'] != duplicate['path']
        assert Path(named['path']).parent == tmp_path / 'data' / 'workspaces'
        assert Path(named['path']).is_dir()
        session = client.post('/api/sessions', json={}).json()
        url = f"/api/sessions/{session['id']}/workspace"
        default = client.get(url).json()
        assert re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]+', Path(default['path']).name)
        changed = client.put(url, json={'group_id': group['id'], 'execution_mode': 'local'}).json()
        assert changed['path'] == group['path']
        with database_connection(client.app.state.database_path) as db:
            db.execute("INSERT INTO messages(id,session_id,role,content) VALUES('branch-user',?,'user','question')", (session['id'],))
        branch = client.post(f"/api/sessions/{session['id']}/branch", json={'message_id': 'branch-user', 'content': 'branch'}).json()
        assert client.get(f"/api/sessions/{branch['id']}/workspace").json()['group_id'] == group['id']
        default_session = client.post('/api/sessions', json={}).json()
        default_url = f"/api/sessions/{default_session['id']}/workspace"
        default_original = client.get(default_url).json()
        with database_connection(client.app.state.database_path) as db:
            db.execute("INSERT INTO messages(id,session_id,role,content) VALUES('default-user',?,'user','question')", (default_session['id'],))
        default_branch = client.post(f"/api/sessions/{default_session['id']}/branch", json={'message_id': 'default-user', 'content': 'branch'}).json()
        default_copy = client.get(f"/api/sessions/{default_branch['id']}/workspace").json()
        assert default_copy['path'] == default_original['path']
        assert Path(default_copy['path']).is_dir()
    with TestClient(app()) as client:
        assert client.get(url).json() == changed
        reset = client.put(url, json={'group_id': None, 'execution_mode': 'sandbox'}).json()
        assert reset['path'] is None
        assert reset['execution_mode'] == 'sandbox'

def test_omitted_and_explicit_local_workspaces_never_check_docker(tmp_path):
    app = create_app(tmp_path / 'local.sqlite3', data_dir=tmp_path / 'data')
    is_configured = Mock(side_effect=AssertionError('local workspaces must not check Docker'))
    app.state.sandbox_executor.is_configured = is_configured

    with TestClient(app) as client:
        omitted = client.post('/api/sessions', json={}).json()
        explicit = client.post(
            '/api/sessions',
            json={'workspace': {'group_id': None, 'execution_mode': 'local'}},
        ).json()
        assert omitted['workspace']['execution_mode'] == 'local'
        assert explicit['workspace']['execution_mode'] == 'local'
        assert Path(omitted['workspace']['path']).is_dir()
        assert Path(explicit['workspace']['path']).is_dir()

        with database_connection(app.state.database_path) as connection:
            assert debug_workspace(
                connection, SimpleNamespace(app=app), None,
            )['execution_mode'] == 'local'
            assert debug_workspace(
                connection, SimpleNamespace(app=app),
                WorkspaceChoice(execution_mode='local'),
            )['execution_mode'] == 'local'

        with database_connection(app.state.database_path) as connection:
            modes = connection.execute(
                'SELECT execution_mode FROM sessions WHERE id IN (?, ?)',
                (omitted['id'], explicit['id']),
            ).fetchall()
        assert len(modes) == 2
        assert {row['execution_mode'] for row in modes} == {'local'}

    is_configured.assert_not_called()


def test_async_sandbox_probe_is_awaited_without_calling_sync_probe(tmp_path):
    class AsyncProbe:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.sync_calls = 0

        async def is_configured_async(self):
            self.started.set()
            await self.release.wait()
            return True

        def is_configured(self):
            self.sync_calls += 1
            raise AssertionError('async routes must not call the synchronous probe')

    async def run():
        app = create_app(tmp_path / 'async-probe.sqlite3', data_dir=tmp_path / 'data')
        probe = AsyncProbe()
        app.state.sandbox_executor = probe
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as client:
                session = (await client.post('/api/sessions', json={})).json()
                task = asyncio.create_task(client.put(
                    f"/api/sessions/{session['id']}/workspace",
                    json={'group_id': None, 'execution_mode': 'sandbox'},
                ))
                await asyncio.wait_for(probe.started.wait(), timeout=1)
                assert not task.done()
                probe.release.set()
                response = await asyncio.wait_for(task, timeout=1)
                assert response.status_code == 200, response.text
                assert response.json()['path'] is None
        assert probe.sync_calls == 0

    asyncio.run(run())


def test_subclass_sync_checker_override_takes_precedence_over_inherited_async_checker(tmp_path):
    class BaseProbe:
        async def is_configured_async(self):
            return False

        def is_configured(self):
            return False

    class SyncOverrideProbe(BaseProbe):
        def __init__(self):
            self.sync_calls = 0

        def is_configured(self):
            self.sync_calls += 1
            return True

    async def run():
        app = create_app(tmp_path / 'subclass-probe.sqlite3', data_dir=tmp_path / 'data')
        probe = SyncOverrideProbe()
        app.state.sandbox_executor = probe
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as client:
                response = await client.post('/api/sessions', json={
                    'workspace': {'group_id': None, 'execution_mode': 'sandbox'},
                })
                assert response.status_code == 201, response.text
                assert response.json()['workspace']['execution_mode'] == 'sandbox'
        assert probe.sync_calls == 1

    asyncio.run(run())


def test_debug_uses_thread_for_blocking_sync_probe(tmp_path):
    class BlockingProbe:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def is_configured(self):
            self.started.set()
            self.release.wait(timeout=2)
            return True

    async def provider(_request):
        return sse({'content': 'done'})

    async def run():
        app = create_app(
            tmp_path / 'debug-blocking-probe.sqlite3',
            data_dir=tmp_path / 'data',
            http_transport=httpx.MockTransport(provider),
        )
        probe = BlockingProbe()
        app.state.sandbox_executor = probe
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as client:
                model_id = create_test_model(app)
                agent = (await client.post('/api/agents', json={
                    'name': 'Blocking Debug', 'model_config_id': model_id,
                })).json()
                task = asyncio.create_task(client.post(f"/api/agents/{agent['id']}/debug", json={
                    'config': {'model_config_id': model_id},
                    'workspace': {'group_id': None, 'execution_mode': 'sandbox'},
                    'messages': [{'role': 'user', 'content': 'test'}],
                }))
                await asyncio.wait_for(asyncio.to_thread(probe.started.wait, 1), timeout=2)
                assert not task.done()
                await asyncio.sleep(0)
                assert not task.done()
                probe.release.set()
                response = await asyncio.wait_for(task, timeout=2)
                assert response.status_code == 200
                assert 'event: done' in response.text

    asyncio.run(run())


def test_workspace_update_rechecks_active_session_after_async_probe(tmp_path):
    class GatedProbe:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def is_configured_async(self):
            self.started.set()
            await self.release.wait()
            return True

    async def run():
        app = create_app(tmp_path / 'workspace-race.sqlite3', data_dir=tmp_path / 'data')
        probe = GatedProbe()
        app.state.sandbox_executor = probe
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as client:
                session = (await client.post('/api/sessions', json={})).json()
                task = asyncio.create_task(client.put(
                    f"/api/sessions/{session['id']}/workspace",
                    json={'group_id': None, 'execution_mode': 'sandbox'},
                ))
                await asyncio.wait_for(probe.started.wait(), timeout=1)
                app.state.active_sessions.add(session['id'])
                probe.release.set()
                response = await asyncio.wait_for(task, timeout=1)
                assert response.status_code == 409
                assert '运行' in response.json()['detail']
                app.state.active_sessions.discard(session['id'])
                with database_connection(app.state.database_path) as connection:
                    row = connection.execute(
                        'SELECT execution_mode FROM sessions WHERE id=?', (session['id'],)
                    ).fetchone()
                assert row['execution_mode'] == 'local'

    asyncio.run(run())


def test_explicit_sandbox_requires_configuration_for_session_and_debug(tmp_path):
    app = create_app(tmp_path / 'unavailable.sqlite3', data_dir=tmp_path / 'data')
    is_configured = Mock(return_value=False)
    app.state.sandbox_executor.is_configured = is_configured

    with TestClient(app) as client:
        session = client.post('/api/sessions', json={}).json()
        response = client.put(
            f"/api/sessions/{session['id']}/workspace",
            json={'group_id': None, 'execution_mode': 'sandbox'},
        )
        assert response.status_code == 409
        assert 'Docker' in response.json()['detail']
        assert client.get(f"/api/sessions/{session['id']}/workspace").json()['execution_mode'] == 'local'

        agent = create_test_agent(client, {'name': 'Debug'}).json()
        response = client.post(f"/api/agents/{agent['id']}/debug", json={
            'config': {'model_config_id': agent['model_config_id']},
            'workspace': {'group_id': None, 'execution_mode': 'sandbox'},
            'messages': [{'role': 'user', 'content': 'test'}],
        })
        assert response.status_code == 409
        assert 'Docker' in response.json()['detail']

    assert is_configured.call_count == 2


def test_explicit_sandbox_without_group_has_no_host_workspace(tmp_path):
    app = create_app(tmp_path / 'sandbox.sqlite3', data_dir=tmp_path / 'data')
    app.state.sandbox_executor.is_configured = Mock(return_value=True)

    with TestClient(app) as client:
        session_response = client.post(
            '/api/sessions',
            json={'workspace': {'group_id': None, 'execution_mode': 'sandbox'}},
        )
        assert session_response.status_code == 201, session_response.text
        session = session_response.json()
        assert session['workspace'] == {'group_id': None, 'name': None, 'path': None, 'execution_mode': 'sandbox'}
        assert not (tmp_path / 'data' / 'workspaces').exists()

        read = client.get(f"/api/sessions/{session['id']}/workspace")
        assert read.status_code == 200
        assert read.json()['path'] is None

    with database_connection(app.state.database_path) as connection:
        row = connection.execute(
            'SELECT default_workspace, execution_mode FROM sessions WHERE id=?',
            (session['id'],),
        ).fetchone()
    assert row['default_workspace'] is None
    assert row['execution_mode'] == 'sandbox'


def test_stored_sandbox_is_rejected_at_read_and_run_without_rewriting_mode(tmp_path):
    app = create_app(tmp_path / 'stored-sandbox.sqlite3', data_dir=tmp_path / 'data')
    app.state.sandbox_executor.is_configured = Mock(return_value=False)

    with TestClient(app) as client:
        session = client.post('/api/sessions', json={}).json()
        with database_connection(app.state.database_path) as connection:
            connection.execute(
                "UPDATE sessions SET execution_mode='sandbox', default_workspace=NULL WHERE id=?",
                (session['id'],),
            )

        read = client.get(f"/api/sessions/{session['id']}/workspace")
        assert read.status_code == 409
        assert 'Docker' in read.json()['detail']

        model_id = create_test_model(app)
        run = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={'content': 'must not run locally', 'model_config_id': model_id},
        )
        assert run.status_code == 409
        assert 'Docker' in run.json()['detail']

    with database_connection(app.state.database_path) as connection:
        row = connection.execute(
            'SELECT default_workspace, execution_mode FROM sessions WHERE id=?',
            (session['id'],),
        ).fetchone()
    assert row['default_workspace'] is None
    assert row['execution_mode'] == 'sandbox'


def test_debug_workspace_does_not_create_host_directory_for_explicit_sandbox(tmp_path):
    from backend.app.routes.workspaces import WorkspaceChoice, debug_workspace

    app = create_app(tmp_path / 'debug-sandbox.sqlite3', data_dir=tmp_path / 'data')
    app.state.sandbox_executor.is_configured = Mock(return_value=True)
    with database_connection(app.state.database_path) as connection:
        workspace_root = app.state.data_dir / 'workspaces'
        result = debug_workspace(
            connection, SimpleNamespace(app=app), WorkspaceChoice(execution_mode='sandbox'),
            sandbox_validated_mode='sandbox',
        )

    assert result == {'path': None, 'execution_mode': 'sandbox'}
    assert not workspace_root.exists()


def test_branch_preserves_automatically_created_default_workspace(tmp_path):
    app = create_app(tmp_path / 'branch.sqlite3', data_dir=tmp_path / 'data')
    with TestClient(app) as client:
        session = client.post('/api/sessions', json={}).json()
        original = client.get(f"/api/sessions/{session['id']}/workspace").json()
        with database_connection(app.state.database_path) as connection:
            connection.execute(
                "INSERT INTO messages(id,session_id,role,content) VALUES('branch-source',?,'user','question')",
                (session['id'],),
            )
        branch = client.post(
            f"/api/sessions/{session['id']}/branch",
            json={'message_id': 'branch-source', 'content': 'follow-up'},
        ).json()
        copied = client.get(f"/api/sessions/{branch['id']}/workspace").json()

    assert copied['path'] == original['path']
    assert copied['execution_mode'] == original['execution_mode']



def test_agent_code_enabled_session_and_debug_default_to_local(tmp_path):
    app = create_app(tmp_path / 'defaults.sqlite3', data_dir=tmp_path / 'data')
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Code', 'sandbox_code_enabled': True}).json()
        session = client.post('/api/sessions', json={'agent_id': agent['id']}).json()
        assert session['workspace']['execution_mode'] == 'local'
        with database_connection(app.state.database_path) as connection:
            debug = debug_workspace(
                connection, SimpleNamespace(app=app), None, sandbox_default=True,
            )
        assert debug['execution_mode'] == 'local'


def test_invalid_workspace_group_paths_are_rejected_without_groups(tmp_path):
    app = create_app(tmp_path / 'invalid-paths.sqlite3', data_dir=tmp_path / 'data')
    with TestClient(app) as client:
        for path in ('relative', str(tmp_path / 'missing')):
            assert client.post('/api/workspaces', json={'path': path}).status_code == 422
        assert client.get('/api/workspaces').json() == []


@pytest.mark.parametrize('scope', ['workspace', 'published', 'debug'])
@pytest.mark.parametrize('mode,approved', [('local', False), ('local', True), ('sandbox', True)])
def test_generated_python_uses_selected_directory_only_after_approval(tmp_path, mode, approved, scope):
    from backend.app.runtime.docker_sandbox import DockerSandboxUnavailable

    source = '''from pathlib import Path

def run(arguments):
    Path('example.txt').write_text(arguments['value'], encoding='utf-8')
    return str(Path.cwd())
'''
    results = []

    async def provider(request):
        body = json.loads(await request.aread())
        last = body['messages'][-1]
        if last['role'] == 'tool':
            results.append(json.loads(last['content']))
            return sse({'content': 'done'})
        tool = next(tool for tool in body['tools'] if 'Python' in tool['function']['description'])
        return invoke(tool['function']['name'], {'code': source, 'arguments': {'value': 'example'}})

    async def run():
        app = create_app(tmp_path / 'db.sqlite3', data_dir=tmp_path / 'data', secret_store=Secrets(),
                         http_transport=httpx.MockTransport(provider))
        if mode == 'sandbox':
            app.state.sandbox_executor.is_configured = lambda: True

        async def unavailable(*args):
            raise DockerSandboxUnavailable('Test sandbox unavailable')

        app.state.sandbox_executor.execute_python_async = unavailable
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
                model = create_test_model(app)
                group = (await client.post('/api/workspaces', json={'name': 'Test group'})).json()
                path = Path(group['path'])
                agent = None
                if scope != 'workspace':
                    agent = (await client.post('/api/agents', json={
                        'name': 'Code Agent', 'model_config_id': model, 'sandbox_code_enabled': True,
                    })).json()
                session = (await client.post('/api/sessions', json={
                    **({'agent_id': agent['id']} if scope == 'published' else {}),
                    'workspace': {'group_id': group['id'], 'execution_mode': mode},
                })).json()
                url = f"/api/sessions/{session['id']}"
                if scope == 'debug':
                    debug_url = f"/api/agents/{agent['id']}/debug"
                    payload = {
                        'config': {'model_config_id': model, 'sandbox_code_enabled': True,
                                   'selected_local_tools': [], 'selected_mcp_tools': []},
                        'workspace': {'group_id': group['id'], 'execution_mode': mode},
                        'approval_mode': 'user_approval',
                        'messages': [{'role': 'user', 'content': 'example'}],
                    }
                    task = asyncio.create_task(client.post(debug_url, json=payload))
                else:
                    task = asyncio.create_task(client.post(url + '/messages', json={
                        'content': 'example', 'model_config_id': model, 'approval_mode': 'user_approval',
                    }))
                try:
                    async with asyncio.timeout(5):
                        while not app.state.pending_approvals and not task.done():
                            await asyncio.sleep(.01)
                        assert app.state.pending_approvals
                        assert not (path / 'example.txt').exists()
                        if scope != 'debug':
                            assert (await client.put(url + '/workspace', json={})).status_code == 409
                        approval = next(iter(app.state.pending_approvals))
                        assert (await client.post(f'/api/approvals/{approval}', json={'approved': approved})).status_code == 200
                        response = await task
                    assert 'event: done' in response.text
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                if mode == 'local' and approved and os.name == 'nt':
                    assert Path(results[0]['result']) == path
                    assert (path / 'example.txt').read_text(encoding='utf-8') == 'example'
                else:
                    assert not (path / 'example.txt').exists()
                    assert 'error' in results[0]

    asyncio.run(run())


def test_posix_local_code_ignores_trailing_result_shaped_output():
    from backend.app.runtime.local_code import _parse_posix_output

    stdout = b'tool output\nHARNESS_TOOL_RESULT:test-token:{"result":"actual"}\n{"result":"trailing"}\n'

    assert _parse_posix_output(stdout, 'test-token') == 'actual'


def test_posix_local_code_parser_prefers_authenticated_result_over_pre_marker_legacy_noise():
    from backend.app.runtime.local_code import _parse_posix_output

    stdout = b'{"result":"noise"}\nHARNESS_TOOL_RESULT:test-token:{"result":"actual"}\n'

    assert _parse_posix_output(stdout, 'test-token') == 'actual'


def test_posix_local_code_parser_keeps_legacy_worker_compatibility():
    from backend.app.runtime.local_code import _parse_posix_output

    assert _parse_posix_output(b'{"result":"legacy"}\n', 'new-token') == 'legacy'


def test_local_code_timeout_stops_child_and_preserves_workspace(tmp_path):
    from backend.app.runtime.local_code import LocalCodeExecutor
    from backend.app.runtime.windows_tool import WindowsToolError

    async def run():
        with pytest.raises(WindowsToolError, match='超时'):
            await LocalCodeExecutor(timeout=0.2).execute(
                'import time\ntime.sleep(10)\n', {}, cwd=tmp_path,
            )
    asyncio.run(run())
    assert list(tmp_path.iterdir()) == []
