import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.db import database_connection
from backend.app.main import create_app
from model_test_utils import create_test_agent, create_test_model
from test_chat_options import Secrets, invoke, sse
from backend.app.tools import ToolDefinition
from backend.app.runtime.a2a import DelegationBudget
from backend.app.runtime.agent_runner import AgentTaskRunner
from backend.app.runtime.model_rounds import MaxRoundsExceeded


def test_agent_round_limit_counts_text_only_model_calls_and_stops_at_limit(tmp_path):
    calls = []
    tool_name = 'repeat'
    async def provider(request):
        body = json.loads(await request.aread())
        calls.append(body)
        if len(calls) <= 2:
            return invoke(tool_name, {})
        return sse({'content': f'partial-{len(calls)}'})
    app = create_app(tmp_path / 'rounds.sqlite3', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    app.state.tool_registry.register(ToolDefinition(tool_name, 'Repeat read-only action', {'type':'object','properties':{}}, 'read_only', lambda _: 'ok'))
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name':'Limited','max_rounds':2}).json()
        session = client.post('/api/sessions', json={'agent_id':agent['id']}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={'content':'go'})
        assert response.status_code == 200
        events = [json.loads(frame.split('\ndata: ',1)[1]) for frame in response.text.split('\n\n') if frame.startswith('event: trace\n')]
        assert 'event: done' not in response.text
        assert '已达到本次请求的最大模型轮次（2）' in response.text
        assert [event['event_type'] for event in events].count('max_rounds_reached') == 1
        assert len(calls) == 2


def test_workspace_chat_ignores_agent_round_limit(tmp_path):
    calls = []
    tool_name = 'repeat'
    async def provider(request):
        body = json.loads(await request.aread())
        calls.append(body)
        if len(calls) <= 2:
            return invoke(tool_name, {})
        return sse({'content':'finished'})
    app = create_app(tmp_path / 'workspace-rounds.sqlite3', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    def execute(_): return 'ok'
    app.state.tool_registry.register(ToolDefinition(tool_name, 'Repeat read-only action', {'type':'object','properties':{}}, 'read_only', execute))
    with TestClient(app) as client:
        model_id = create_test_model(client, model='m')
        session = client.post('/api/sessions', json={}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={'content':'go','model_config_id':model_id})
        assert response.status_code == 200 and 'event: done' in response.text
    assert len(calls) == 3


def test_session_final_text_on_last_round_succeeds(tmp_path):
    calls = []
    async def provider(request):
        calls.append(request)
        return sse({'content': 'finished'})
    app = create_app(tmp_path / 'exact.sqlite3', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Exact', 'max_rounds': 1}).json()
        session = client.post('/api/sessions', json={'agent_id': agent['id']}).json()
        response = client.post(f"/api/sessions/{session['id']}/messages", json={'content': 'go'})
    assert 'event: done' in response.text
    assert 'max_rounds_reached' not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize('limit,expected_calls,completed', [(1, 1, False), (2, 2, True)])
def test_compaction_counts_once_before_next_model_call(tmp_path, limit, expected_calls, completed):
    calls = []
    async def provider(request):
        body = json.loads(await request.aread())
        calls.append(body)
        if not body['stream']:
            return httpx.Response(200, json={'choices': [{'message': {'content': 'history summary'}}]})
        return sse({'content': 'finished'})
    app = create_app(tmp_path / 'compaction.sqlite3', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Compact', 'context_window': 1024, 'max_rounds': limit}).json()
        session = client.post('/api/sessions', json={'agent_id': agent['id']}).json()
        with database_connection(app.state.database_path) as db:
            db.executemany("INSERT INTO messages(id,session_id,role,content) VALUES(?,?,'user',?)",
                           [(uuid4().hex, session['id'], 'history ' * 100) for _ in range(8)])
        response = client.post(f"/api/sessions/{session['id']}/messages", json={'content': 'go'})
    assert len(calls) == expected_calls
    assert 'context_compacted' in response.text
    assert ('event: done' in response.text) is completed
    assert ('max_rounds_reached' in response.text) is not completed


@pytest.mark.parametrize('limit', [9, -1])
def test_delegated_agent_can_finish_after_eight_rounds(tmp_path, monkeypatch, limit):
    calls = []
    async def stream(*args, **kwargs):
        calls.append(1)
        if len(calls) < 9:
            yield {'type': 'tool_calls', 'tool_calls': [{'id': str(len(calls)), 'name': 'missing', 'arguments': {}}]}
        else:
            yield {'type': 'text', 'text': 'finished'}
    monkeypatch.setattr('backend.app.runtime.agent_runner.stream_chat_events', stream)
    app = create_app(tmp_path / 'delegated.sqlite3', data_dir=tmp_path / 'data', secret_store=Secrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Delegated', 'max_rounds': limit}).json()
        runner = AgentTaskRunner(app, None)
        result = asyncio.run(runner(agent['id'], 'go', DelegationBudget(['a', 'b', 'c'])))
    assert result == 'finished'
    assert len(calls) == 9


def test_delegated_agent_stops_before_call_above_limit(tmp_path, monkeypatch):
    calls = []
    async def stream(*args, **kwargs):
        calls.append(1)
        yield {'type': 'tool_calls', 'tool_calls': [{'id': str(len(calls)), 'name': 'missing', 'arguments': {}}]}
    monkeypatch.setattr('backend.app.runtime.agent_runner.stream_chat_events', stream)
    app = create_app(tmp_path / 'delegated-limit.sqlite3', data_dir=tmp_path / 'data', secret_store=Secrets())
    with TestClient(app) as client:
        agent = create_test_agent(client, {'name': 'Delegated', 'max_rounds': 9}).json()
        runner = AgentTaskRunner(app, None)
        with pytest.raises(MaxRoundsExceeded):
            asyncio.run(runner(agent['id'], 'go', DelegationBudget(['a', 'b', 'c'])))
    assert len(calls) == 9
