"""SSRF and destination validation for research retrieval.

Domain allowlisting alone is not SSRF protection. This module validates the
scheme, port, hostname, every DNS-resolved address, and every redirect target.
Production deployments must additionally enforce the same policy at the egress
proxy so a second DNS resolution inside the HTTP stack cannot bypass it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.google.internal.",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".localdomain",
    ".home.arpa",
    ".lan",
)
_ALLOWED_SCHEMES = frozenset({"https"})
_ALLOWED_PORTS = frozenset({443})


class BlockedDestinationError(Exception):
    """Raised when a URL or DNS result violates the egress policy."""

    def __init__(self, reason: str, *, url: str | None = None) -> None:
        self.reason = reason
        self.url = url
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ValidatedUrl:
    url: str
    scheme: str
    host: str
    port: int
    resolved_ips: tuple[str, ...]


def _canonical_host(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as err:
        raise BlockedDestinationError("Hostname is not valid IDNA.") from err


def is_blocked_ip(value: str) -> bool:
    """Return True for any address that is not a global unicast destination."""

    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True

    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped

    return not (
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def hostname_allowed(host: str, patterns: tuple[str, ...] | list[str]) -> bool:
    normalized = _canonical_host(host)
    for pattern in patterns:
        candidate = pattern.strip().lower().rstrip(".")
        if not candidate:
            continue
        if candidate.startswith("*."):
            suffix = candidate[2:]
            if normalized == suffix or normalized.endswith(f".{suffix}"):
                return True
        elif normalized == candidate:
            return True
    return False


def validate_url(
    url: str,
    *,
    allowed_domains: tuple[str, ...] | list[str] | None = None,
    allowed_schemes: frozenset[str] = _ALLOWED_SCHEMES,
    allowed_ports: frozenset[int] = _ALLOWED_PORTS,
) -> ValidatedUrl:
    """Validate a URL without performing DNS resolution."""

    if not isinstance(url, str) or not url.strip():
        raise BlockedDestinationError("URL is empty.", url=url)
    if len(url) > 2048:
        raise BlockedDestinationError("URL exceeds the maximum length.", url=url)
    if any(ord(char) < 32 for char in url):
        raise BlockedDestinationError("URL contains control characters.", url=url)

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in allowed_schemes:
        raise BlockedDestinationError(f"Scheme '{scheme}' is not allowed.", url=url)
    if parts.username or parts.password:
        raise BlockedDestinationError("URL credentials are not allowed.", url=url)
    if not parts.hostname:
        raise BlockedDestinationError("URL has no hostname.", url=url)

    host = _canonical_host(parts.hostname)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise BlockedDestinationError("IP-literal URLs are not allowed.", url=url)

    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise BlockedDestinationError("Hostname is internal or reserved.", url=url)

    port = parts.port or (443 if scheme == "https" else 80)
    if port not in allowed_ports:
        raise BlockedDestinationError(f"Port {port} is not allowed.", url=url)

    if allowed_domains is not None and not hostname_allowed(host, allowed_domains):
        raise BlockedDestinationError("Hostname is not on the approved source allowlist.", url=url)

    normalized = urlunsplit((scheme, f"{host}:{port}" if port != 443 else host, parts.path or "/", parts.query, ""))
    return ValidatedUrl(url=normalized, scheme=scheme, host=host, port=port, resolved_ips=())


async def resolve_and_validate(
    url: str,
    *,
    allowed_domains: tuple[str, ...] | list[str] | None = None,
) -> ValidatedUrl:
    """Resolve a URL and reject if any resolved address is not public."""

    validated = validate_url(url, allowed_domains=allowed_domains)
    addresses = await _resolve(validated.host, validated.port)
    if not addresses:
        raise BlockedDestinationError("DNS resolution returned no addresses.", url=url)
    blocked = [address for address in addresses if is_blocked_ip(address)]
    if blocked:
        raise BlockedDestinationError(
            "DNS resolved to a private, loopback, link-local, reserved, or metadata address.",
            url=url,
        )
    return ValidatedUrl(
        url=validated.url,
        scheme=validated.scheme,
        host=validated.host,
        port=validated.port,
        resolved_ips=tuple(addresses),
    )


async def _resolve(host: str, port: int) -> tuple[str, ...]:
    def _lookup() -> tuple[str, ...]:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return ()
        addresses: list[str] = []
        for info in infos:
            sockaddr = info[4]
            if sockaddr and isinstance(sockaddr[0], str):
                addresses.append(sockaddr[0])
        return tuple(dict.fromkeys(addresses))

    return await asyncio.to_thread(_lookup)


def assert_resolution_stable(first: ValidatedUrl, second: ValidatedUrl) -> None:
    """Reject a host whose second resolution differs from the validated one.

    This is a defence-in-depth check. It does not replace connection-time
    validation in the egress proxy, but it catches an obvious DNS-rebinding
    attempt between policy evaluation and request dispatch.
    """

    if set(first.resolved_ips) != set(second.resolved_ips):
        raise BlockedDestinationError(
            "DNS resolution changed between validation and request dispatch.",
            url=first.url,
        )
    if any(is_blocked_ip(address) for address in second.resolved_ips):
        raise BlockedDestinationError("Rebinding resolved to a blocked address.", url=first.url)


def validate_redirect(
    location: str,
    *,
    allowed_domains: tuple[str, ...] | list[str],
) -> ValidatedUrl:
    """Validate a redirect target before following it."""

    return validate_url(location, allowed_domains=allowed_domains)
