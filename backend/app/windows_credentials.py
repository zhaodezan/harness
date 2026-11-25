"""Per-user Windows DPAPI storage for model keys when Credential Manager fails."""
import ctypes
from ctypes import wintypes
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(value: bytes, entropy: bytes, *, decrypt: bool = False) -> bytes:
    data = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    salt = (ctypes.c_ubyte * len(entropy)).from_buffer_copy(entropy)
    source, extra, result = _Blob(len(data), data), _Blob(len(salt), salt), _Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    local_free = ctypes.WinDLL('kernel32').LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    # UI forbidden; no LOCAL_MACHINE flag, so the ciphertext is user scoped.
    if not function(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        if decrypt:
            ctypes.memset(result.data, 0, result.size)
        local_free(result.data)


class EncryptedModelKeys:
    def __init__(self, directory: Path):
        self.directory = directory

    @staticmethod
    def _identity(name: str) -> bytes:
        return sha256(f'local-agent-harness:dpapi:v1:{name}'.encode()).digest()

    def path(self, name: str) -> Path:
        return self.directory / (self._identity(name).hex() + '.dpapi')

    def read(self, name: str) -> tuple[bool, str | None]:
        try:
            ciphertext = self.path(name).read_bytes()
        except FileNotFoundError:
            return False, None
        plaintext = _dpapi(ciphertext, self._identity(name), decrypt=True)
        # An empty encrypted value marks a deletion while the vault is unavailable.
        return True, plaintext.decode('utf-8') or None

    def write(self, name: str, value: str | None) -> None:
        ciphertext = _dpapi((value or '').encode('utf-8'), self._identity(name))
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = None
        try:
            with NamedTemporaryFile(dir=self.directory, suffix='.tmp', delete=False) as stream:
                temp = Path(stream.name)
                stream.write(ciphertext)
                stream.flush()
            temp.replace(self.path(name))
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
