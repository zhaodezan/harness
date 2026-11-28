import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.dify_plugins.manager import InstallError
from backend.app.main import create_app
from backend.app.dify_plugins.tests.samples import package
from backend.app.tools import ApprovalRequired, ToolRegistry, filter_tools_for_agent


class Secrets:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name)
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


class Market:
    async def resolve(self, value):
        return {'plugin_id': 'sample/echo', 'version': '1.2.3', 'identifier': 'sample/echo:1.2.3@' + 'a' * 64}
    async def download(self, identifier): return package()
    async def search(self, query, page): return {'items': [], 'total': 0, 'page_size': 20}


class Runner:
    def __init__(self, *, fail=False): self.fail = fail; self.installed = set(); self.calls = []
    async def exists(self, image): return image in self.installed
    async def build(self, data, info):
        if self.fail: raise InstallError('docker unavailable')
        image = 'harness-dify-plugin:' + info.digest
        self.installed.add(image)
        return image
    async def remove(self, image): self.installed.discard(image)
    async def run(self, image, request, *, network=False):
        self.calls.append((image, request, network))
        return [{'type': 'text', 'text': 'executed'}]


def app(tmp_path, *, fail=False, secret_store=None):
    return create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=secret_store or Secrets(),
                      dify_marketplace=Market(), dify_runner=Runner(fail=fail))


def test_preview_install_register_and_run_only_selected_side_effect_tool(tmp_path):
    registry = ToolRegistry()
    value = app(tmp_path)
    value.state.tool_registry = registry
    with TestClient(value) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'https://marketplace.dify.ai/plugin/sample/echo'}).json()
        assert preview['tools'][0]['name'] == 'echo'
        assert 'credentials' in preview and 'token' in preview
        installed = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False})
        assert installed.status_code == 201
        value.state.dify_runner.calls.clear()
        tool = client.get('/api/dify-plugins/tools').json()[0]
        assert tool['id'].startswith('dify:sample/echo:1.2.3:')
        registry_tools = registry.list()
        assert filter_tools_for_agent(registry_tools, [], []) == []
        assert len(filter_tools_for_agent(registry_tools, [], [tool['id']])) == 1
        with pytest.raises(ApprovalRequired):
            asyncio.run(registry.execute(tool['name'], {'text': 'ok'}, approved=False))
        assert value.state.dify_runner.calls == []
        result = asyncio.run(registry.execute(tool['name'], {'text': 'ok'}, approved=True))
        assert result == 'executed'
        assert value.state.dify_runner.calls[-1][1]['arguments'] == {'text': 'ok', 'count': 1}
        assert value.state.dify_runner.calls[-1][2] is False
        assert client.delete(f"/api/dify-plugins/installed/{installed.json()['id']}").status_code == 204
        assert registry.get_definition(tool['name']) is None


def test_failed_install_leaves_no_package_or_registered_tool(tmp_path):
    value = app(tmp_path, fail=True)
    with TestClient(value) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'https://marketplace.dify.ai/plugin/sample/echo'}).json()
        result = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False})
        assert result.status_code == 503
        assert client.get('/api/dify-plugins/installed').json() == []
        assert client.get('/api/dify-plugins/tools').json() == []


def test_credentials_never_return_and_network_needs_opt_in(tmp_path):
    store = Secrets()
    value = app(tmp_path, secret_store=store)
    with TestClient(value) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'sample/echo'}).json()
        installed = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False}).json()
        settings = client.put(f"/api/dify-plugins/installed/{installed['id']}/settings", json={'credentials': {'sample_key': 'secret-value'}, 'network_enabled': True})
        assert settings.status_code == 200
        assert 'secret-value' not in settings.text
        fields = client.get('/api/dify-plugins/installed').json()[0]['credentials']
        assert fields['sample_key'] == {'type': 'secret-input', 'label': 'Example key', 'required': False, 'configured': True}
        assert 'secret-value' not in client.get('/api/dify-plugins/tools').text
        assert 'secret-value' not in value.state.dify_manager.catalog.read_text()
        assert store.get(f"dify:{installed['id']}:sample_key") == 'secret-value'


def test_restore_fails_closed_for_non_boolean_network_authorization(tmp_path):
    runner = Runner()
    value = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(),
                       dify_marketplace=Market(), dify_runner=runner)
    with TestClient(value) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'sample/echo'}).json()
        installed = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False}).json()
        catalog = value.state.dify_manager.catalog
        records = json.loads(catalog.read_text())
        records[0]['network_enabled'] = 'false'
        catalog.write_text(json.dumps(records))
    restarted = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=Secrets(),
                           dify_marketplace=Market(), dify_runner=runner)
    with TestClient(restarted) as client:
        tool = client.get('/api/dify-plugins/tools').json()[0]
        assert asyncio.run(restarted.state.tool_registry.execute(tool['name'], {'text': 'ok'}, approved=True)) == 'executed'
    assert runner.calls[-1][2] is False
    assert installed['id'] == restarted.state.dify_manager.list_installed()[0]['id']


def test_blank_credential_update_preserves_saved_secret(tmp_path):
    store = Secrets()
    value = app(tmp_path, secret_store=store)
    with TestClient(value) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'sample/echo'}).json()
        installed = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False}).json()
        assert client.put(f"/api/dify-plugins/installed/{installed['id']}/settings", json={
            'credentials': {'sample_key': 'original'}, 'network_enabled': False,
        }).status_code == 200
        assert client.put(f"/api/dify-plugins/installed/{installed['id']}/settings", json={
            'credentials': {'sample_key': ''}, 'network_enabled': False,
        }).status_code == 200
        assert store.get(f"dify:{installed['id']}:sample_key") == 'original'
        assert client.get('/api/dify-plugins/installed').json()[0]['credentials']['sample_key']['configured'] is True


def test_credential_update_rolls_back_secret_when_catalog_persist_fails(tmp_path, monkeypatch):
    store = Secrets()
    value = app(tmp_path, secret_store=store)
    with TestClient(value) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'sample/echo'}).json()
        installed = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False}).json()
        assert client.put(f"/api/dify-plugins/installed/{installed['id']}/settings", json={
            'credentials': {'sample_key': 'original'}, 'network_enabled': False,
        }).status_code == 200
        def fail_persist(records=None):
            raise OSError('disk full')
        monkeypatch.setattr(value.state.dify_manager, '_persist', fail_persist)
        response = client.put(f"/api/dify-plugins/installed/{installed['id']}/settings", json={
            'credentials': {'sample_key': 'replacement'}, 'network_enabled': True,
        })
        assert response.status_code == 503
        assert store.get(f"dify:{installed['id']}:sample_key") == 'original'
        assert client.get('/api/dify-plugins/installed').json()[0]['credentials']['sample_key']['configured'] is True



    value = app(tmp_path)
    with TestClient(value) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'sample/echo'}).json()
        installed = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False}).json()
        value.state.dify_runner.installed.clear()
    restarted = app(tmp_path)
    with TestClient(restarted) as client:
        item = client.get('/api/dify-plugins/installed').json()[0]
        assert item['status'] == 'image_missing'
        assert item['id'] == installed['id']
        assert client.get('/api/dify-plugins/tools').json() == []


def test_invalid_credential_validation_does_not_echo_secret(tmp_path):
    with TestClient(app(tmp_path)) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'sample/echo'}).json()
        installed = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False}).json()
        secret = 'private-' + 'x' * 5000
        response = client.put(f"/api/dify-plugins/installed/{installed['id']}/settings", json={
            'credentials': secret, 'network_enabled': False,
        })
        assert response.status_code == 422
        assert secret not in response.text


def test_uninstall_keeps_retryable_record_when_credential_cleanup_fails(tmp_path):
    class FailingSecrets(Secrets):
        def __init__(self): super().__init__(); self.fail = True
        def delete(self, name):
            if self.fail: raise RuntimeError('vault unavailable')
            super().delete(name)

    store = FailingSecrets()
    value = app(tmp_path, secret_store=store)
    with TestClient(value) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'sample/echo'}).json()
        installed = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False}).json()
        result = client.delete(f"/api/dify-plugins/installed/{installed['id']}")
        assert result.status_code == 503
        pending = client.get('/api/dify-plugins/installed').json()[0]
        assert pending['status'] == 'cleanup_pending'
        assert client.get('/api/dify-plugins/tools').json() == []
        store.fail = False
        assert client.delete(f"/api/dify-plugins/installed/{installed['id']}").status_code == 204
        assert client.get('/api/dify-plugins/installed').json() == []


def test_cleanup_pending_plugin_stays_disabled_after_restart(tmp_path):
    class FailingSecrets(Secrets):
        def __init__(self): super().__init__(); self.fail = True
        def delete(self, name):
            if self.fail: raise RuntimeError('vault unavailable')
            super().delete(name)

    store = FailingSecrets()
    runner = Runner()
    value = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=store,
                       dify_marketplace=Market(), dify_runner=runner)
    with TestClient(value) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'sample/echo'}).json()
        installed = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False}).json()
        assert client.put(f"/api/dify-plugins/installed/{installed['id']}/settings", json={
            'credentials': {'sample_key': 'saved'}, 'network_enabled': False,
        }).status_code == 200
        assert client.delete(f"/api/dify-plugins/installed/{installed['id']}").status_code == 503
    restarted = create_app(tmp_path / 'db.sqlite', data_dir=tmp_path / 'data', secret_store=store,
                           dify_marketplace=Market(), dify_runner=runner)
    with TestClient(restarted) as client:
        assert client.get('/api/dify-plugins/installed').json()[0]['status'] == 'cleanup_pending'
        assert client.get('/api/dify-plugins/tools').json() == []
        store.fail = False
        assert client.delete(f"/api/dify-plugins/installed/{installed['id']}").status_code == 204


def test_missing_image_can_be_rebuilt_in_place_without_losing_settings(tmp_path):
    value = app(tmp_path)
    with TestClient(value) as client:
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'sample/echo'}).json()
        installed = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False}).json()
        value.state.dify_runner.installed.clear()
    restarted = app(tmp_path)
    with TestClient(restarted) as client:
        assert client.get('/api/dify-plugins/installed').json()[0]['status'] == 'image_missing'
        preview = client.post('/api/dify-plugins/preview/market', json={'link': 'sample/echo'}).json()
        rebuilt = client.post('/api/dify-plugins/install', json={'token': preview['token'], 'network_enabled': False})
        assert rebuilt.status_code == 201
        assert rebuilt.json()['id'] == installed['id']
        assert client.get('/api/dify-plugins/installed').json()[0]['status'] == 'ready'


def test_dify_upload_without_content_length_reaches_multipart_parser(tmp_path):
    data = package()
    boundary = 'dify-upload-boundary'
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.difypkg"\r\n'
        'Content-Type: application/octet-stream\r\n\r\n'
    ).encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    chunks = (body[index:index + 4096] for index in range(0, len(body), 4096))
    with TestClient(app(tmp_path)) as client:
        response = client.post('/api/dify-plugins/preview/file', content=chunks, headers={
            'content-type': f'multipart/form-data; boundary={boundary}',
        })
    assert response.status_code == 200, response.text
    assert response.json()['plugin_id'] == 'sample/echo'


def test_dify_upload_rejects_oversized_declared_request_before_parsing(tmp_path):
    with TestClient(app(tmp_path)) as client:
        response = client.post('/api/dify-plugins/preview/file', content=b'', headers={
            'content-type': 'multipart/form-data; boundary=abc',
            'content-length': str(20 * 1024 * 1024),
        })
    assert response.status_code == 413
