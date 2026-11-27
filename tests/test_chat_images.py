import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.chat_images import collect_skill_images
from backend.app.main import create_app
from model_test_utils import create_test_agent
from test_chat_options import Secrets, invoke, sse


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN8kAAAAASUVORK5CYII=')


def test_image_snapshots_deduplicate_bytes_not_the_output_filename(tmp_path):
    workspace = tmp_path / 'output'
    workspace.mkdir()
    path = workspace / 'same.png'
    path.write_bytes(PNG)
    source = json.dumps({'delivery': [{'preview_path': str(path)}]})
    state = SimpleNamespace(data_dir=tmp_path / 'data')
    choice = {'execution_mode': 'local', 'path': str(workspace)}
    seen = set()

    def collect():
        return collect_skill_images(state, source, choice, debug=False, seen=seen, limit=1)[0]

    (first,) = collect()
    assert not collect()
    path.write_bytes(PNG + b'changed')
    (second,) = collect()
    assert first['id'] != second['id']
    assert (state.data_dir / 'chat-images' / 'saved' / first['id']).read_bytes() == PNG


def image_events(response):
    return [json.loads(frame.split('\ndata: ', 1)[1])['images']
            for frame in response.text.split('\n\n') if frame.startswith('event: images\n')]


@pytest.mark.parametrize('scope', ['workspace', 'published', 'debug'])
def test_skill_images_are_workspace_scoped_and_displayed_on_the_reply(tmp_path, scope):
    data = tmp_path / 'data'
    skill = data / 'plugins' / 'skills' / 'demo'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: demo\ndescription: Picture demo\n---\nUse CLI.', encoding='utf-8')
    (skill / 'image.py').write_text('print(42)', encoding='utf-8')
    workspace = tmp_path / 'current workspace'
    workspace.mkdir()
    picture = workspace / '示例.png'
    picture.write_bytes(PNG)
    outside = tmp_path / 'not-in-workspace.png'
    outside.write_bytes(PNG)
    disguised = workspace / 'not-an-image.png'
    disguised.write_text('<html>not an image</html>', encoding='utf-8')
    seen = []

    async def execute(path, argv, *, cwd):
        assert cwd == workspace
        return json.dumps({'delivery': [
            {'preview_path': str(outside)}, {'preview_path': str(disguised)},
            {'preview_path': str(picture), 'image_name': '测试图' + chr(0xd800)},
            {'preview_path': str(picture), 'image_name': 'duplicate'},
        ]}, ensure_ascii=True)

    async def provider(request):
        body = json.loads(await request.aread())
        seen.append(body)
        if len(seen) == 1 and scope == 'workspace':
            return invoke(next(t['function']['name'] for t in body['tools'] if 'load_skill' in t['function']['name']), {'skill_name': 'demo'})
        if len(seen) == (2 if scope == 'workspace' else 1):
            return invoke(next(t['function']['name'] for t in body['tools'] if 'run_python_cli' in t['function']['name']), {'skill_name': 'demo', 'path': 'image.py', 'argv': []})
        if body['messages'][-1]['role'] == 'tool':
            result = json.loads(body['messages'][-1]['content'])['result']
            assert outside.name not in result and str(workspace) not in result
            assert picture.name in result
        return sse({'content': '![Fake](/unregistered.png)'})

    def app_factory():
        return create_app(tmp_path / 'harness.sqlite3', data_dir=data, secret_store=Secrets(), http_transport=httpx.MockTransport(provider))

    app = app_factory()
    app.state.skill_cli_executor = SimpleNamespace(available=True, execute_script=execute)
    with TestClient(app) as client:
        group = client.post('/api/workspaces', json={'path': str(workspace)}).json()
        choice = {'group_id': group['id'], 'execution_mode': 'local'}
        agent = create_test_agent(client, {'name': 'Images', 'selected_skills': ['demo']}).json()
        if scope == 'debug':
            response = client.post(f"/api/agents/{agent['id']}/debug", json={
                'config': {'model_config_id': agent['model_config_id'], 'selected_skills': ['demo']},
                'messages': [{'role': 'user', 'content': 'draw'}], 'workspace': choice,
            })
        else:
            session = client.post('/api/sessions', json={'workspace': choice, **({'agent_id': agent['id']} if scope == 'published' else {})}).json()['id']
            path = f'/api/sessions/{session}/messages'
            payload = {'content': 'draw', 'model_config_id': agent['model_config_id']}
            if scope == 'workspace':
                payload['capabilities'] = {'skills': ['demo']}
            response = client.post(path, json=payload)
        assert response.status_code == 200
        (images,) = image_events(response)
        (image,) = images
        assert image['label'] == '测试图?'
        assert outside.name not in response.text
        assert image['url'].startswith('/api/')
        assert str(workspace) not in json.dumps(image)
        picture.unlink()  # The source can be archived or changed without losing the displayed snapshot.
        media = client.get(image['url'])
        assert media.status_code == 200 and media.content == PNG
        assert media.headers['content-type'] == 'image/png'
        assert media.headers['x-content-type-options'] == 'nosniff'
        assert media.headers['cross-origin-resource-policy'] == 'same-origin'
        assert client.get(image['url'], headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        if scope == 'debug':
            assert client.get('/api/sessions').json() == []
            return
        (reply,) = [msg for msg in client.get(path).json() if msg['role'] == 'assistant']
        assert reply['images'] == images
        second = client.post(path, json={'content': 'again', 'model_config_id': agent['model_config_id']})
        assert image_events(second) == []
        target = client.get(path).json()[-2]['id']
        branch = client.post(f'/api/sessions/{session}/branch', json={'message_id': target, 'content': 'fork'}).json()['id']
        branch_image = next(msg for msg in client.get(f'/api/sessions/{branch}/messages').json() if msg['role'] == 'assistant')['images'][0]
        assert branch_image['url'] != image['url'] and client.get(branch_image['url']).content == PNG
        assert client.get(image['url'].replace(reply['id'], '0' * 32)).status_code == 404
    with TestClient(app_factory()) as reloaded:
        assert reloaded.get(path).json()[1]['images'] == images
        assert reloaded.get(image['url']).content == PNG
        assert reloaded.delete(f'/api/sessions/{session}').status_code == 204
        assert reloaded.get(image['url']).status_code == 404
        assert reloaded.get(branch_image['url']).content == PNG
