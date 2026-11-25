import os
from pathlib import Path
from threading import RLock
from typing import Protocol

import keyring

from backend.app.windows_credentials import EncryptedModelKeys


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class SystemSecretStore:
    """Store API credentials using the host operating system's credential vault."""

    service_name = "local-agent-harness"

    def __init__(self, *, model_fallback_dir: Path | None = None):
        self._fallback = EncryptedModelKeys(model_fallback_dir) if os.name == 'nt' and model_fallback_dir is not None else None
        self._lock = RLock()

    def _model_store(self, name: str) -> EncryptedModelKeys | None:
        return self._fallback if name.startswith('model:') and name.endswith(':api_key') else None

    def get(self, name: str) -> str | None:
        with self._lock:
            fallback = self._model_store(name)
            if fallback is not None:
                found, value = fallback.read(name)
                if found:
                    return value
            return keyring.get_password(self.service_name, name)

    def set(self, name: str, value: str) -> None:
        with self._lock:
            fallback = self._model_store(name)
            if fallback is not None and fallback.path(name).is_file():
                fallback.write(name, value)
                return
            try:
                keyring.set_password(self.service_name, name, value)
            except Exception:
                if fallback is None:
                    raise
                fallback.write(name, value)

    def delete(self, name: str) -> None:
        with self._lock:
            fallback = self._model_store(name)
            if fallback is not None and fallback.path(name).is_file():
                # Prevent an older vault copy from resurfacing if the vault can't delete it yet.
                fallback.write(name, None)
                try:
                    self._delete_from_vault(name)
                except Exception:
                    return
                fallback.path(name).unlink(missing_ok=True)
            else:
                self._delete_from_vault(name)

    def _delete_from_vault(self, name: str) -> None:
        try:
            keyring.delete_password(self.service_name, name)
        except keyring.errors.PasswordDeleteError:
            pass
