import os
import re
import tempfile
from pathlib import Path

import anyio

from prediction_ai.core.exceptions import StorageException

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,500}$")


def validate_storage_key(key: str) -> str:
    if not _SAFE_KEY.fullmatch(key) or ".." in key.split("/") or key.startswith("/"):
        raise StorageException("Invalid storage object key.")
    return key


class LocalFileStorage:
    """Private, atomic local filesystem storage used for development and tests."""

    def __init__(self, root: str) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)

    def _path(self, key: str) -> Path:
        validate_storage_key(key)
        candidate = (self._root / key).resolve()
        if self._root not in candidate.parents and candidate != self._root:
            raise StorageException("Storage path escapes the configured root.")
        return candidate

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        del content_type
        path = self._path(key)
        await anyio.to_thread.run_sync(self._write_atomic, path, data)

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".upload-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        os.chmod(path, 0o600)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await anyio.to_thread.run_sync(path.read_bytes)
        except OSError as err:
            raise StorageException("Stored screenshot could not be read.") from err

    async def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            await anyio.to_thread.run_sync(path.unlink)
        except FileNotFoundError:
            return
        except OSError as err:
            raise StorageException("Stored screenshot could not be deleted.") from err

    async def health(self) -> bool:
        try:
            await anyio.to_thread.run_sync(self._health_sync)
            return True
        except OSError:
            return False

    def _health_sync(self) -> None:
        fd, name = tempfile.mkstemp(dir=self._root, prefix=".health-")
        os.close(fd)
        os.unlink(name)
