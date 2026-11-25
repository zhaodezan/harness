import os

import pytest

from backend.app.secrets import SystemSecretStore


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI integration')
def test_model_vault_falls_back_to_user_encryption_and_never_restores_old_key(tmp_path, monkeypatch):
    key = 'model:test:api_key'
    vault = {key: 'old-vault-secret'}
    unavailable = True

    def write(service, name, value):
        if unavailable:
            raise OSError('credential vault unavailable')
        vault[name] = value

    def delete(service, name):
        if unavailable:
            raise OSError('credential vault unavailable')
        vault.pop(name, None)

    monkeypatch.setattr('keyring.get_password', lambda service, name: vault.get(name))
    monkeypatch.setattr('keyring.set_password', write)
    monkeypatch.setattr('keyring.delete_password', delete)

    store = SystemSecretStore(model_fallback_dir=tmp_path)
    assert store.get(key) == 'old-vault-secret'
    store.set(key, 'replacement-secret')
    assert b'replacement-secret' not in next(tmp_path.iterdir()).read_bytes()
    store = SystemSecretStore(model_fallback_dir=tmp_path)
    assert store.get(key) == 'replacement-secret'
    unavailable = False
    store.set(key, 'updated-secret')
    assert store.get(key) == 'updated-secret'
    assert vault[key] == 'old-vault-secret'
    unavailable = True
    store.delete(key)
    unavailable = False
    assert SystemSecretStore(model_fallback_dir=tmp_path).get(key) is None
    store.delete(key)
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI integration')
def test_vault_does_not_claim_success_if_encrypted_storage_is_unwritable(tmp_path, monkeypatch):
    def fail(*_args):
        raise OSError('credential vault unavailable')

    monkeypatch.setattr('keyring.set_password', fail)
    file = tmp_path / 'not-a-directory'
    file.touch()
    with pytest.raises(OSError):
        SystemSecretStore(model_fallback_dir=file).set('model:test:api_key', 'not-persisted')
