import asyncio
import uuid
from functools import lru_cache

import jwt
from fastapi import Header
from pydantic import BaseModel

from prediction_ai.core.config import Settings, get_settings
from prediction_ai.core.exceptions import AuthenticationException

_DEV_TOKEN = "Bearer dev-test-token"
_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "HS256")


class AuthenticatedUser(BaseModel):
    id: uuid.UUID
    email: str | None = None
    role: str = "authenticated"
    tier: str = "standard"


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=300)


def _development_user(settings: Settings) -> AuthenticatedUser:
    try:
        user_id = uuid.UUID(settings.DEV_USER_ID)
    except ValueError as err:  # pragma: no cover - configuration error
        raise AuthenticationException("DEV_USER_ID is not a valid UUID.") from err
    return AuthenticatedUser(id=user_id, email="developer@localhost", role="authenticated")


async def _decode_token(token: str, settings: Settings) -> dict[str, object]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as err:
        raise AuthenticationException("Malformed authentication token.") from err

    algorithm = str(header.get("alg", ""))
    if algorithm not in _ALGORITHMS:
        raise AuthenticationException("Unsupported token signing algorithm.")

    options = {
        "require": ["exp", "sub"],
        "verify_exp": True,
        "verify_aud": True,
        "verify_iss": bool(settings.jwt_issuer),
    }

    try:
        if algorithm == "HS256":
            if settings.SUPABASE_JWT_SECRET is None:
                raise AuthenticationException(
                    "HS256 tokens are not configured for this deployment."
                )
            key: object = settings.SUPABASE_JWT_SECRET.get_secret_value()
        else:
            jwks_url = settings.jwks_url
            if not jwks_url:
                raise AuthenticationException("JWKS verification is not configured.")
            signing_key = await asyncio.to_thread(
                _jwks_client(jwks_url).get_signing_key_from_jwt, token
            )
            key = signing_key.key

        payload = jwt.decode(
            token,
            key,  # type: ignore[arg-type]
            algorithms=[algorithm],
            audience=settings.SUPABASE_JWT_AUDIENCE,
            issuer=settings.jwt_issuer,
            options=options,  # type: ignore[arg-type]
            leeway=10,
        )
    except jwt.ExpiredSignatureError as err:
        raise AuthenticationException("Authentication token has expired.") from err
    except jwt.InvalidAudienceError as err:
        raise AuthenticationException("Authentication token audience is invalid.") from err
    except jwt.InvalidIssuerError as err:
        raise AuthenticationException("Authentication token issuer is invalid.") from err
    except jwt.PyJWTError as err:
        raise AuthenticationException("Invalid authentication token.") from err

    if not isinstance(payload, dict):
        raise AuthenticationException("Invalid authentication token payload.")
    return payload


async def verify_supabase_jwt(
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    """Verify a Supabase access token and return the caller identity."""
    settings = get_settings()

    if not settings.AUTH_REQUIRED:
        if authorization in (None, "", _DEV_TOKEN):
            return _development_user(settings)

    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationException(
            "Missing or malformed Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise AuthenticationException(
            "Missing authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = await _decode_token(token, settings)
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationException("Token payload is missing the subject identifier.")

    try:
        user_id = uuid.UUID(subject)
    except ValueError as err:
        raise AuthenticationException("Token subject is not a valid user identifier.") from err

    email = payload.get("email")
    role = payload.get("role", "authenticated")
    return AuthenticatedUser(
        id=user_id,
        email=email if isinstance(email, str) else None,
        role=role if isinstance(role, str) else "authenticated",
    )
