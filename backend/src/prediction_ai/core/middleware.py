import json
import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from prediction_ai.core.config import Settings

logger = structlog.get_logger(__name__)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cross-origin-resource-policy", b"same-site"),
)


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    if client and client[0]:
        return str(client[0])
    headers = dict(scope.get("headers") or [])
    forwarded = headers.get(b"x-forwarded-for")
    if forwarded and isinstance(forwarded, bytes):
        return str(forwarded.decode("latin-1").split(",")[0].strip())
    return "unknown"


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if isinstance(key, bytes) and key.lower() == name and isinstance(value, bytes):
            return str(value.decode("latin-1"))
    return None


class RequestContextMiddleware:
    """Attach request IDs and structured request logging to every response."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_id = _header(scope, b"x-request-id")
        request_id = (
            incoming_id
            if incoming_id and _REQUEST_ID_PATTERN.fullmatch(incoming_id)
            else f"req_{uuid.uuid4().hex}"
        )
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        scope["state"]["client_ip"] = _client_ip(scope)

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_started = True
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                remaining = {key for key, _ in headers}
                for key, value in _SECURITY_HEADERS:
                    if key not in remaining:
                        headers.append((key, value))
                if self.settings.ENVIRONMENT == "production":
                    headers.append(
                        (b"strict-transport-security", b"max-age=63072000; includeSubDomains")
                    )
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "Unhandled request error",
                method=scope.get("method"),
                path=scope.get("path"),
                duration_ms=duration_ms,
            )
            if not response_started:
                body = json.dumps(
                    {
                        "error": {
                            "code": "INTERNAL_SERVER_ERROR",
                            "message": "An unexpected error occurred.",
                        },
                        "meta": {"request_id": request_id},
                    }
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"x-request-id", request_id.encode("latin-1")),
                            *_SECURITY_HEADERS,
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
            else:
                raise
            return
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "HTTP request",
                method=scope.get("method"),
                path=scope.get("path"),
                status_code=status_code,
                duration_ms=duration_ms,
                client_ip=scope["state"].get("client_ip"),
            )
            structlog.contextvars.clear_contextvars()


class BodySizeLimitMiddleware:
    """Reject oversized uploads before multipart parsing allocates memory/disk."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.max_body_bytes = settings.MAX_UPLOAD_BYTES + 1024 * 1024

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                length = -1
            if length < 0 or length > self.max_body_bytes:
                request_id = scope.get("state", {}).get("request_id", "")
                body = json.dumps(
                    {
                        "error": {
                            "code": "PAYLOAD_TOO_LARGE",
                            "message": (
                                f"Request body exceeds the {self.max_body_bytes} byte limit."
                            ),
                        },
                        "meta": {"request_id": request_id},
                    }
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return

        await self.app(scope, receive, send)
