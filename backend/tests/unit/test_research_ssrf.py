"""Security tests for SSRF and destination validation (§17.3).

These are release blockers. Every test here represents a class of SSRF attack
the runtime must reject before the browser/HTTP layer is ever reached.
"""

from __future__ import annotations

import pytest

from prediction_ai.research.ssrf import (
    BlockedDestinationError,
    hostname_allowed,
    is_blocked_ip,
    validate_url,
)


class TestIsBlockedIp:
    # Loopback
    def test_ipv4_loopback(self) -> None:
        assert is_blocked_ip("127.0.0.1") is True

    def test_ipv4_loopback_range(self) -> None:
        assert is_blocked_ip("127.255.255.255") is True

    # Private ranges
    def test_ipv4_private_10(self) -> None:
        assert is_blocked_ip("10.0.0.1") is True

    def test_ipv4_private_172(self) -> None:
        assert is_blocked_ip("172.16.0.1") is True

    def test_ipv4_private_192(self) -> None:
        assert is_blocked_ip("192.168.1.1") is True

    # Link-local
    def test_ipv4_link_local(self) -> None:
        assert is_blocked_ip("169.254.169.254") is True  # AWS metadata

    # Cloud metadata
    def test_gcp_metadata_ip(self) -> None:
        assert is_blocked_ip("169.254.169.254") is True

    # Multicast
    def test_ipv4_multicast(self) -> None:
        assert is_blocked_ip("224.0.0.1") is True

    # IPv6 loopback
    def test_ipv6_loopback(self) -> None:
        assert is_blocked_ip("::1") is True

    # IPv6 ULA
    def test_ipv6_ula(self) -> None:
        assert is_blocked_ip("fd00::1") is True

    # IPv6 link-local
    def test_ipv6_link_local(self) -> None:
        assert is_blocked_ip("fe80::1") is True

    # IPv4-mapped IPv6 — CRITICAL: bypass attempt
    def test_ipv4_mapped_ipv6_private(self) -> None:
        assert is_blocked_ip("::ffff:192.168.1.1") is True

    def test_ipv4_mapped_ipv6_loopback(self) -> None:
        assert is_blocked_ip("::ffff:127.0.0.1") is True

    def test_ipv4_mapped_ipv6_metadata(self) -> None:
        assert is_blocked_ip("::ffff:169.254.169.254") is True

    # Unspecified
    def test_unspecified(self) -> None:
        assert is_blocked_ip("0.0.0.0") is True

    # Public addresses — must NOT be blocked
    def test_public_ip_not_blocked(self) -> None:
        assert is_blocked_ip("8.8.8.8") is False

    def test_public_ip_not_blocked_2(self) -> None:
        assert is_blocked_ip("1.1.1.1") is False

    # Invalid — blocked by default
    def test_invalid_string_blocked(self) -> None:
        assert is_blocked_ip("not-an-ip") is True

    def test_empty_string_blocked(self) -> None:
        assert is_blocked_ip("") is True


class TestHostnameAllowed:
    def test_exact_match(self) -> None:
        assert hostname_allowed("example.com", ["example.com"]) is True

    def test_case_insensitive(self) -> None:
        assert hostname_allowed("EXAMPLE.COM", ["example.com"]) is True

    def test_wildcard_subdomain(self) -> None:
        assert hostname_allowed("api.example.com", ["*.example.com"]) is True

    def test_wildcard_does_not_match_apex(self) -> None:
        # *.example.com should match api.example.com but also the apex
        assert hostname_allowed("example.com", ["*.example.com"]) is True

    def test_no_match(self) -> None:
        assert hostname_allowed("evil.com", ["example.com"]) is False

    def test_subdomain_bypass_attempt(self) -> None:
        assert hostname_allowed("evil.com.example.com", ["example.com"]) is False

    def test_empty_patterns(self) -> None:
        assert hostname_allowed("example.com", []) is False


class TestValidateUrl:
    def test_valid_https_url(self) -> None:
        result = validate_url("https://example.com/path")
        assert result.scheme == "https"
        assert result.host == "example.com"

    def test_rejects_http(self) -> None:
        with pytest.raises(BlockedDestinationError, match="Scheme"):
            validate_url("http://example.com/")

    def test_rejects_ftp(self) -> None:
        with pytest.raises(BlockedDestinationError, match="Scheme"):
            validate_url("ftp://example.com/")

    def test_rejects_file_scheme(self) -> None:
        with pytest.raises(BlockedDestinationError):
            validate_url("file:///etc/passwd")

    def test_rejects_ip_literal_private(self) -> None:
        with pytest.raises(BlockedDestinationError, match="IP-literal"):
            validate_url("https://192.168.1.1/")

    def test_rejects_ip_literal_loopback(self) -> None:
        with pytest.raises(BlockedDestinationError, match="IP-literal"):
            validate_url("https://127.0.0.1/")

    def test_rejects_localhost(self) -> None:
        with pytest.raises(BlockedDestinationError, match="internal"):
            validate_url("https://localhost/secret")

    def test_rejects_localhost_subdomain(self) -> None:
        with pytest.raises(BlockedDestinationError, match="internal"):
            validate_url("https://anything.localhost/")

    def test_rejects_internal_suffix(self) -> None:
        with pytest.raises(BlockedDestinationError, match="internal"):
            validate_url("https://db.internal/")

    def test_rejects_non_443_port(self) -> None:
        with pytest.raises(BlockedDestinationError, match="Port"):
            validate_url("https://example.com:8080/")

    def test_rejects_credentials_in_url(self) -> None:
        with pytest.raises(BlockedDestinationError, match="credentials"):
            validate_url("https://user:pass@example.com/")

    def test_rejects_empty_url(self) -> None:
        with pytest.raises(BlockedDestinationError, match="empty"):
            validate_url("")

    def test_rejects_oversized_url(self) -> None:
        with pytest.raises(BlockedDestinationError, match="length"):
            validate_url("https://example.com/" + "a" * 2050)

    def test_rejects_control_characters(self) -> None:
        with pytest.raises(BlockedDestinationError, match="control"):
            validate_url("https://example.com/\x00inject")

    def test_domain_allowlist_blocks_non_listed(self) -> None:
        with pytest.raises(BlockedDestinationError, match="allowlist"):
            validate_url("https://example.com/", allowed_domains=["other.com"])

    def test_domain_allowlist_passes_listed(self) -> None:
        result = validate_url("https://example.com/", allowed_domains=["example.com"])
        assert result.host == "example.com"

    def test_rejects_metadata_hostname(self) -> None:
        with pytest.raises(BlockedDestinationError):
            validate_url("https://metadata.google.internal/")

    def test_rejects_unusual_port(self) -> None:
        with pytest.raises(BlockedDestinationError, match="Port"):
            validate_url("https://example.com:22/")
