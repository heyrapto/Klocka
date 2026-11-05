from collections.abc import Mapping
from typing import Any


class DomainException(Exception):
    """Base class for safe, client-facing application errors.

    ``analysis_status`` is the terminal workflow state persisted when the error
    happens inside the asynchronous analysis pipeline. API-only errors leave it
    as ``None``.
    """

    code: str = "DOMAIN_ERROR"
    status_code: int = 400
    analysis_status: str | None = None
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: list[dict[str, Any]] | None = None,
        headers: Mapping[str, str] | None = None,
        analysis_status: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if analysis_status is not None:
            self.analysis_status = analysis_status
        if retryable is not None:
            self.retryable = retryable
        self.details = details or []
        self.headers = dict(headers or {})


class ValidationException(DomainException):
    code = "VALIDATION_ERROR"
    status_code = 400
    analysis_status = "FAILED"


class PayloadTooLargeException(DomainException):
    code = "PAYLOAD_TOO_LARGE"
    status_code = 413
    analysis_status = "FAILED"


class UnsupportedMediaTypeException(DomainException):
    code = "UNSUPPORTED_MEDIA_TYPE"
    status_code = 415
    analysis_status = "FAILED"


class RateLimitExceededException(DomainException):
    code = "RATE_LIMITED"
    status_code = 429


class AuthenticationException(DomainException):
    code = "AUTHENTICATION_FAILED"
    status_code = 401


class AuthorizationException(DomainException):
    code = "PERMISSION_DENIED"
    status_code = 403


class NotFoundException(DomainException):
    code = "NOT_FOUND"
    status_code = 404


class ConflictException(DomainException):
    code = "CONFLICT"
    status_code = 409


class IdempotencyConflictException(DomainException):
    code = "IDEMPOTENCY_KEY_REUSED"
    status_code = 409


class ExtractionUnavailableException(DomainException):
    code = "EXTRACTION_UNAVAILABLE"
    status_code = 503
    analysis_status = "EXTRACTION_FAILED"


class ExtractionFailedException(DomainException):
    code = "EXTRACTION_FAILED"
    status_code = 422
    analysis_status = "EXTRACTION_FAILED"


class MarketNotFoundException(DomainException):
    code = "MARKET_NOT_FOUND"
    status_code = 422
    analysis_status = "MARKET_NOT_FOUND"


class MarketAmbiguousException(DomainException):
    code = "MARKET_AMBIGUOUS"
    status_code = 409
    analysis_status = "NEEDS_USER_CONFIRMATION"


class MarketNotVerifiedException(DomainException):
    code = "MARKET_NOT_VERIFIED"
    status_code = 422
    analysis_status = "VERIFICATION_FAILED"


class UnsupportedMarketException(DomainException):
    code = "UNSUPPORTED_MARKET"
    status_code = 422
    analysis_status = "UNSUPPORTED_MARKET"


class MarketNotActiveException(DomainException):
    code = "MARKET_NOT_ACTIVE"
    status_code = 422
    analysis_status = "UNSUPPORTED_MARKET"


class InsufficientDataException(DomainException):
    code = "INSUFFICIENT_DATA"
    status_code = 422
    analysis_status = "INSUFFICIENT_DATA"


class DataStaleException(DomainException):
    code = "DATA_STALE"
    status_code = 503
    analysis_status = "DATA_STALE"


class ForecastFailedException(DomainException):
    code = "FORECAST_FAILED"
    status_code = 500
    analysis_status = "FORECAST_FAILED"


class StorageException(DomainException):
    code = "STORAGE_ERROR"
    status_code = 500
    analysis_status = "FAILED"


class UpstreamProviderException(DomainException):
    code = "UPSTREAM_PROVIDER_ERROR"
    status_code = 502
    retryable = True


class ProviderTimeoutException(DomainException):
    code = "UPSTREAM_TIMEOUT"
    status_code = 504
    retryable = True


class CircuitOpenException(DomainException):
    code = "UPSTREAM_CIRCUIT_OPEN"
    status_code = 503
    retryable = True


class ServiceUnavailableException(DomainException):
    code = "SERVICE_UNAVAILABLE"
    status_code = 503
