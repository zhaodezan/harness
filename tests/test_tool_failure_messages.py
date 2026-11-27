import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from model_test_utils import create_test_agent
from test_chat_options import Secrets, invoke, sse


@pytest.mark.parametrize('debug', [True, False])
def test_unconfigured_sandbox_is_distinct_from_denied_approval(tmp_path, debug):
    requests = []

    async def provider(request):
        body = json.loads(await request.aread())
        requests.append(body)
        if len(requests) == 1:
            return invoke('get_weather', {'city': 'Beijing'})
        return sse({'content': 'Tool unavailable'})

    app = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(), http_transport=httpx.MockTransport(provider))
    async def configured_at_selection():
        return True
    app.state.sandbox_executor.is_configured_async = configured_at_selection
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        tool = client.post('/api/tools', json={'source': 'def get_weather(city: str) -> str:\n    """Example greeting."""\n    return f"Example only: {city}"'}).json()
        assert tool['risk'] == 'read_only'
        agent = create_test_agent(client, {'name': 'Test', 'selected_local_tools': [tool['id']]}).json()
        if debug:
            response = client.post(f"/api/agents/{agent['id']}/debug", json={
                'config': {'model_config_id': agent['model_config_id'], 'selected_local_tools': [tool['id']]},
                'messages': [{'role': 'user', 'content': 'Weather in Beijing'}],
                'workspace': {'execution_mode': 'sandbox'},
            })
        else:
            session = client.post('/api/sessions', json={'agent_id': agent['id'], 'workspace': {'execution_mode': 'sandbox'}}).json()
            response = client.post(f"/api/sessions/{session['id']}/messages", json={'content': 'Weather in Beijing'})
        assert response.status_code == 200
        assert 'event: done' in response.text and 'event: approval_required' not in response.text
        assert 'Docker 沙箱未配置' in response.text
    tool_result = json.loads(requests[-1]['messages'][-1]['content'])
    assert 'Docker 沙箱未配置' in tool_result['error']
    assert '未批准' not in tool_result['error']
