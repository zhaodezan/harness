import json

import httpx
from fastapi.testclient import TestClient

from backend.app import db as schema
from backend.app.db import database_connection, initialize_database
from backend.app.main import create_app
from model_test_utils import create_test_agent, create_test_model
from test_chat_options import Secrets, invoke, sse


def test_workspace_chat_works_without_agents_and_requires_a_model(tmp_path):
    requests = []

    async def provider(request):
        requests.append(json.loads(await request.aread()))
        return sse({'content': 'workspace reply'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        session = client.post('/api/sessions', json={'title': 'Workspace'})
        assert session.status_code == 201, session.text
        session = session.json()
        assert session['agent_id'] is None
        assert client.get('/api/sessions/chat-options').json()['defaults']['agents'] == []
        url = f"/api/sessions/{session['id']}/messages"
        assert client.post(url, json={'content': 'no model'}).status_code == 409
        assert client.get(url).json() == []
        model_id = create_test_model(client, model='workspace')
        result = client.post(url, json={'content': 'hello', 'model_config_id': model_id})
        assert 'workspace reply' in result.text and 'event: done' in result.text
        assert client.get('/api/agents').json() == []
    assert len(requests) == 1 and requests[0]['model'] == 'workspace'
    assert requests[0]['messages'] == [{'role': 'user', 'content': 'hello'}]
    assert not requests[0].get('tools')


def test_workspace_search_branch_history_and_delegation_are_separate(tmp_path):
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if len(requests) == 1:
            return invoke(body['tools'][0]['function']['name'], {'agent_id': agent['id'], 'task': 'Summarize this'})
        return sse({'content': 'done'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Child', 'model': 'child', 'system_prompt': 'AGENT_ONLY'}).json()
        model_id = create_test_model(client, model='workspace')
        work = client.post('/api/sessions', json={'title': 'Shared term workspace'}).json()
        dedicated = client.post('/api/sessions', json={'title': 'Shared term agent', 'agent_id': agent['id']}).json()
        assert client.get('/api/sessions/chat-options').json()['options']['agents'] == [{'id': agent['id'], 'name': 'Child'}]
        response = client.post(f"/api/sessions/{work['id']}/messages", json={
            'content': 'hello', 'model_config_id': model_id, 'capabilities': {'agents': [agent['id']]},
        })
        assert '"status":"completed"' in response.text, response.text
        message = client.get(f"/api/sessions/{work['id']}/messages").json()[0]
        branch = client.post(f"/api/sessions/{work['id']}/branch", json={'message_id': message['id'], 'content': 'edit'}).json()
        assert branch['agent_id'] is None
        assert {s['id'] for s in client.get('/api/sessions', params={'workspace': 'true'}).json()} == {work['id'], branch['id']}
        assert [s['id'] for s in client.get('/api/sessions', params={'agent_id': agent['id']}).json()] == [dedicated['id']]
        hits = client.get('/api/sessions/search', params={'q': 'Shared term', 'workspace': 'true'}).json()
        assert sorted(s['session_id'] for s in hits) == sorted([work['id'], branch['id']])
        assert [s['session_id'] for s in client.get('/api/sessions/search', params={'q': 'Shared term', 'agent_id': agent['id']}).json()] == [dedicated['id']]
        assert client.delete(f"/api/agents/{agent['id']}").status_code == 204
        assert len(client.get('/api/sessions', params={'workspace': 'true'}).json()) == 2
    assert [r['model'] for r in requests] == ['workspace', 'child', 'workspace']
    assert 'AGENT_ONLY' not in json.dumps(requests[0]) and 'AGENT_ONLY' in json.dumps(requests[1])


def test_workspace_chat_uses_global_memory_without_agent_memory(tmp_path):
    requests = []

    async def provider(request):
        requests.append(json.loads(await request.aread()))
        return sse({'content': 'done'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Dedicated'}).json()
        model_id = create_test_model(client, model='workspace')
        assert client.post('/api/memories', json={'content': 'Atlas project GLOBAL_MEMORY'}).status_code == 201
        assert client.post('/api/memories', json={
            'content': 'Atlas project DEDICATED_MEMORY', 'scope': 'agent', 'agent_id': agent['id'],
        }).status_code == 201
        for agent_id, model_options in [(None, {'model_config_id': model_id}), (agent['id'], {})]:
            session = client.post('/api/sessions', json={'agent_id': agent_id}).json()
            response = client.post(f"/api/sessions/{session['id']}/messages", json={'content': 'Review Atlas project', **model_options})
            assert 'event: done' in response.text, response.text
    workspace, dedicated = (json.dumps(body['messages']) for body in requests)
    assert 'GLOBAL_MEMORY' in workspace and 'DEDICATED_MEMORY' not in workspace
    assert 'GLOBAL_MEMORY' in dedicated and 'DEDICATED_MEMORY' in dedicated


def test_workspace_backup_restores_unbound_sessions_and_messages(tmp_path):
    source = create_app(tmp_path / 'source.sqlite', data_dir=tmp_path / 'source-data', secret_store=Secrets())
    with TestClient(source) as client:
        workspace = client.post('/api/sessions', json={'title': 'Standalone workspace'}).json()
        with database_connection(source.state.database_path) as db:
            db.execute("INSERT INTO messages(id,session_id,role,content) VALUES('msg',?,'user','Keep my workspace history')", (workspace['id'],))
        payload = client.get('/api/data/export').json()
        assert payload['agents'] == [] and payload['sessions'][0]['agent_id'] is None
    target = create_app(tmp_path / 'target.sqlite', data_dir=tmp_path / 'target-data', secret_store=Secrets())
    with TestClient(target) as client:
        result = client.post('/api/data/import', json=payload)
        assert result.status_code == 200, result.text
        restored, = client.get('/api/sessions', params={'workspace': 'true'}).json()
        assert restored['agent_id'] is None and restored['title'] == workspace['title']
        assert restored['id'] != workspace['id']
        assert [message['content'] for message in client.get(f"/api/sessions/{restored['id']}/messages").json()] == ['Keep my workspace history']


def test_v22_upgrade_preserves_existing_history_and_foreign_keys(tmp_path, monkeypatch):
    path = tmp_path / 'old.sqlite'
    with monkeypatch.context() as patch:
        patch.setattr(schema, '_allow_workspace_sessions', lambda connection: None)
        initialize_database(path)
    with database_connection(path) as db:
        assert next(row for row in db.execute('PRAGMA table_info(sessions)') if row['name'] == 'agent_id')['notnull']
        db.execute("INSERT INTO agents(id,name) VALUES('agent','Existing')")
        db.execute("INSERT INTO sessions(id,agent_id) VALUES('root','agent')")
        db.execute("INSERT INTO sessions(id,agent_id,parent_session_id,branched_from_message_id) VALUES('branch','agent','root','msg')")
        db.execute("INSERT INTO messages(id,session_id,role,content) VALUES('msg','root','user','Keep me')")
        db.execute("INSERT INTO context_summaries(id,session_id,run_id,summary,summarized_message_count) VALUES('summary','root','run','Keep summary',2)")
    initialize_database(path)
    with database_connection(path) as db:
        assert db.execute("SELECT agent_id, parent_session_id, branched_from_message_id FROM sessions WHERE id='branch'").fetchone()[:] == ('agent', 'root', 'msg')
        assert db.execute("SELECT content FROM messages").fetchone()[0] == 'Keep me'
        assert db.execute("SELECT summary FROM context_summaries").fetchone()[0] == 'Keep summary'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        db.execute("INSERT INTO sessions(id,agent_id) VALUES('workspace',NULL)")
        db.execute("DELETE FROM agents WHERE id='agent'")
        assert [r[0] for r in db.execute('SELECT id FROM sessions')] == ['workspace']
        assert not db.execute('SELECT * FROM messages').fetchall()
        assert not db.execute('SELECT * FROM context_summaries').fetchall()
