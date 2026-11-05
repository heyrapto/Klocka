import httpx

from prediction_ai.core.config import Settings
from prediction_ai.core.exceptions import StorageException
from prediction_ai.storage.local import validate_storage_key


class SupabaseStorage:
    """Supabase Storage REST client using the service-role credential."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        if not settings.SUPABASE_URL or settings.SUPABASE_SERVICE_ROLE_KEY is None:
            raise StorageException("Supabase storage is not configured.")
        self._base_url = settings.SUPABASE_URL.rstrip("/")
        self._bucket = settings.SUPABASE_STORAGE_BUCKET
        self._client = client
        self._service_key = settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value()

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._service_key,
            "Authorization": f"Bearer {self._service_key}",
        }

    def _object_url(self, key: str) -> str:
        validate_storage_key(key)
        return f"{self._base_url}/storage/v1/object/{self._bucket}/{key}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        headers = {
            **self._headers(),
            "Content-Type": content_type,
            "x-upsert": "true",
            "cache-control": "no-store",
        }
        try:
            response = await self._client.put(
                self._object_url(key), content=data, headers=headers, timeout=30.0
            )
        except httpx.HTTPError as err:
            raise StorageException("Screenshot upload to storage failed.") from err
        if response.status_code >= 400:
            raise StorageException("Screenshot upload to storage was rejected.")

    async def get(self, key: str) -> bytes:
        try:
            response = await self._client.get(self._object_url(key), headers=self._headers())
        except httpx.HTTPError as err:
            raise StorageException("Stored screenshot could not be read.") from err
        if response.status_code == 404:
            raise StorageException("Stored screenshot does not exist.")
        if response.status_code >= 400:
            raise StorageException("Stored screenshot could not be read.")
        return response.content

    async def delete(self, key: str) -> None:
        try:
            response = await self._client.delete(self._object_url(key), headers=self._headers())
        except httpx.HTTPError as err:
            raise StorageException("Stored screenshot could not be deleted.") from err
        if response.status_code >= 400 and response.status_code != 404:
            raise StorageException("Stored screenshot could not be deleted.")

    async def health(self) -> bool:
        url = f"{self._base_url}/storage/v1/bucket/{self._bucket}"
        try:
            response = await self._client.get(url, headers=self._headers(), timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code < 400
