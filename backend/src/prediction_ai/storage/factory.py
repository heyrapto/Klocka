import httpx

from prediction_ai.core.config import Settings
from prediction_ai.storage.base import StorageBackend
from prediction_ai.storage.local import LocalFileStorage
from prediction_ai.storage.supabase import SupabaseStorage


def create_storage(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> StorageBackend:
    if settings.STORAGE_BACKEND == "supabase":
        http_client = client or httpx.AsyncClient()
        return SupabaseStorage(settings, http_client)
    return LocalFileStorage(settings.STORAGE_LOCAL_DIR)
