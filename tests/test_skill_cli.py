import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.runtime.windows_tool import WindowsToolError
from model_test_utils import create_test_agent
from test_chat_options import Secrets, invoke, sse
from test_chat_run_trace import traces


def cli_executor(timeout=30):
    from backend.app.runtime.skill_cli import SkillCLIExecutor

    executor = SkillCLIExecutor(timeout=timeout)
    if not executor.available:
        pytest.skip('requires the Windows PowerShell tool runtime')
    return executor


def test_skill_cli_runs_original_script_with_literal_argv_and_workspace(tmp_path):
    scripts = tmp_path / 'skill' / 'scripts'
    scripts.mkdir(parents=True)
    (scripts / 'sibling.py').write_text('VALUE = 42', encoding='utf-8')
    (scripts / 'pkgutil.py').write_text('raise AssertionError("worker imported a sibling dependency")', encoding='utf-8')
    entry = scripts / 'entry.py'
    entry.write_text('''import argparse, json, sys
from pathlib import Path
from sibling import VALUE
p = argparse.ArgumentParser()
p.add_argument('--message', required=True)
args = p.parse_args()
assert not sys.stdin.read()
print(json.dumps({'value': VALUE, 'message': args.message, 'file': __file__, 'cwd': str(Path.cwd()), 'home': str(Path.home())}))
''', encoding='utf-8')
    workspace = tmp_path / '工作 目录'
    workspace.mkdir()
    message = '文字 with spaces & quotes " and \''
    result = asyncio.run(cli_executor().execute_script(entry, ['--message', message], cwd=workspace))
    assert json.loads(result) == {'value': 42, 'message': message, 'file': str(entry),
                                  'cwd': str(workspace), 'home': str(Path.home())}


@pytest.mark.parametrize(('source', 'diagnostic'), [
    ('import sys\nsys.stderr.write("private-output")\nraise ModuleNotFoundError("private-diagnostic")', '缺少 Python 模块'),
    ('import argparse\np = argparse.ArgumentParser()\np.add_argument("--required", required=True)\np.parse_args()', '退出码 2'),
    ('import sys\nprint("private-diagnostic")\nsys.exit("private-exception")', '退出码 1'),
])
def test_skill_cli_errors_do_not_return_raw_output(tmp_path, source, diagnostic):
    script = tmp_path / 'entry.py'
    script.write_text(source, encoding='utf-8')
    with pytest.raises(WindowsToolError, match=diagnostic) as failure:
        asyncio.run(cli_executor().execute_script(script, [], cwd=tmp_path))
    assert 'private-' not in str(failure.value)


def test_skill_cli_uses_a_bounded_timeout(tmp_path):
    script = tmp_path / 'entry.py'
    script.write_text('import time\ntime.sleep(10)', encoding='utf-8')
    with pytest.raises(WindowsToolError, match='超时'):
        asyncio.run(cli_executor(timeout=0.05).execute_script(script, [], cwd=tmp_path))


@pytest.mark.parametrize(('debug', 'decision'), [
    (debug, decision) for debug in (False, True) for decision in ('approved', 'denied', 'escape', 'sandbox')
] + [(False, 'legacy')])
def test_chat_skill_cli_keeps_approval_and_workspace_boundaries(tmp_path, debug, decision):
    skill_root = tmp_path / ('legacy' if decision == 'legacy' else 'data/plugins/skills')
    folder = skill_root / 'demo'
    folder.mkdir(parents=True)
    (folder / 'SKILL.md').write_text('---\nname: demo\ndescription: Demo\n---\nCLI entry.py', encoding='utf-8')
    entry = folder / 'entry.py'
    entry.write_text('print(42)', encoding='utf-8')
    outside = folder.parent / 'outside.py'
    outside.write_text('raise AssertionError', encoding='utf-8')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    received = []
    executed = []
    argv = ['--message', 'with spaces', '--token', 'dummy-private-token', '--api-key=dummy-private-key']
    provider_cli = None

    async def provider(request):
        nonlocal provider_cli
        body = json.loads(await request.aread())
        received.append(body)
        if len(received) == 1:
            tools = [item['function']['name'] for item in body['tools']]
            provider_cli = next((name for name in tools if 'run_python_cli' in name), None)
            if decision in {'sandbox', 'legacy'}:
                assert provider_cli is None
                return sse({'content': 'done'})
            assert provider_cli is not None
            return invoke(provider_cli, {'skill_name': 'demo', 'path': '../outside.py' if decision == 'escape' else 'entry.py', 'argv': argv})
        assert body['messages'][-2]['tool_calls'][0]['function']['name'] == provider_cli
        return sse({'content': 'done'})

    async def execute(path, args, *, cwd):
        executed.append((path, args, cwd))
        return 'CLI marker'

    app = create_app(tmp_path / 'test.sqlite3', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    app.state.skill_cli_executor = SimpleNamespace(available=True, execute_script=execute)
    app.state.sandbox_executor.is_configured = lambda: True
    with TestClient(app) as client:
        settings = {'name': 'CLI', 'selected_skills': ['demo']}
        if decision == 'legacy':
            settings['skill_directory'] = str(skill_root)
        agent = create_test_agent(client, settings).json()
        group = client.post('/api/workspaces', json={'path': str(workspace)})
        assert group.status_code == 201
        selected_workspace = {'group_id': group.json()['id'], 'execution_mode': 'sandbox' if decision == 'sandbox' else 'local'}
        payload = {'approval_mode': 'allow_all' if decision != 'denied' else 'user_approval', 'workspace': selected_workspace}
        if debug:
            path = f"/api/agents/{agent['id']}/debug"
            payload.update(config={'model_config_id': agent['model_config_id'], 'selected_skills': ['demo']}, messages=[{'role': 'user', 'content': 'Run CLI'}])
        else:
            session = client.post('/api/sessions', json={'agent_id': agent['id'], 'workspace': selected_workspace}).json()
            path = f"/api/sessions/{session['id']}/messages"
            payload.pop('workspace')
            payload.update(content='Run CLI')
        if decision == 'denied':
            from concurrent.futures import ThreadPoolExecutor
            from time import monotonic, sleep

            with ThreadPoolExecutor() as pool:
                response_future = pool.submit(client.post, path, json=payload)
                deadline = monotonic() + 5
                while not app.state.pending_approvals and not response_future.done() and monotonic() < deadline:
                    sleep(0.01)
                pending = list(app.state.pending_approvals)
                assert executed == []
                for approval_id in pending:
                    client.post(f'/api/approvals/{approval_id}', json={'approved': False})
                assert pending
                response = response_future.result(timeout=5)
        else:
            response = client.post(path, json=payload)
    assert response.status_code == 200
    assert 'dummy-private-' not in response.text
    if decision in {'sandbox', 'legacy'}:
        assert len(received) == 1 and executed == []
        return
    assert '[REDACTED]' in response.text
    completion = next(event for event in traces(response) if event['event_type'] == 'tool_completed')
    if decision == 'approved':
        assert executed == [(entry, argv, workspace)]
        assert completion['status'] == 'completed'
        assert 'CLI marker' in completion['tool_details']['output']['text']
    else:
        assert executed == []
        assert completion['status'] == 'failed'
        assert ('未批准' if decision == 'denied' else '相对路径') in completion['tool_details']['error']['text']
