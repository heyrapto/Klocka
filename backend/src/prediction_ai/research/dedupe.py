"""URL normalization, syndication detection, and source-independence grouping.

Three syndicated copies of one wire story count as one underlying reporting
chain. The grouping is deterministic and conservative: uncertain independence is
preserved rather than silently promoted to corroboration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from prediction_ai.research.enums import ClaimStance, SupportState
from prediction_ai.research.models import Claim, DocumentVersion, SourceGroup

_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref",
        "ref_src",
        "source",
        "cmpid",
        "smid",
    }
)
_WIRE_SERVICES = (
    "reuters",
    "associated press",
    "ap news",
    "agence france-presse",
    "afp",
    "pa media",
    "pa sport",
    "bloomberg",
    "dpa",
    "ansa",
    "efe",
    "kyodo",
    "xinhua",
)
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "by",
        "at",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "will",
        "would",
        "could",
        "should",
        "has",
        "have",
        "had",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "his",
        "her",
        "their",
        "after",
        "before",
        "over",
        "under",
        "about",
        "into",
        "than",
        "then",
        "not",
        "no",
    }
)


def normalize_url(url: str) -> str:
    """Normalize a URL for identity without changing its semantic path."""

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))


def title_tokens(title: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN.findall(title.lower())
        if len(token) > 2 and token not in _STOPWORDS
    )


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def detect_wire_service(text: str) -> str | None:
    window = text[:4000].lower()
    for service in _WIRE_SERVICES:
        if re.search(rf"\b{re.escape(service)}\b", window):
            return service
    return None


def _group_id(cluster_key: str) -> str:
    return sha256(cluster_key.encode("utf-8")).hexdigest()[:32]


@dataclass(slots=True)
class _GroupState:
    group_id: str
    cluster_key: str
    origin_url: str
    publisher: str | None
    tokens: frozenset[str]
    wire_service: str | None
    member_count: int = 1
    independence_confidence: str = "uncertain"
    document_ids: list[UUID] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GroupingResult:
    document_groups: dict[UUID, str]
    groups: list[SourceGroup]
    uncertain_independence: bool


def build_source_groups(
    *,
    job_id: UUID,
    versions: list[DocumentVersion],
) -> GroupingResult:
    """Cluster document versions into independent reporting chains."""

    states: list[_GroupState] = []
    document_groups: dict[UUID, str] = {}
    uncertain = False

    for version in sorted(versions, key=lambda item: item.captured_at):
        tokens = title_tokens(version.title)
        wire_service = detect_wire_service(version.text_excerpt or version.title)
        content_hash = version.content_hash

        matched: _GroupState | None = None
        uncertain_match = False
        for state in states:
            if state.wire_service and wire_service and state.wire_service == wire_service:
                matched = state
                break
            if state.cluster_key == content_hash:
                matched = state
                break
            similarity = jaccard(state.tokens, tokens)
            if similarity >= 0.8:
                matched = state
                break
            if similarity >= 0.6:
                uncertain_match = True

        if matched is not None:
            matched.member_count += 1
            matched.document_ids.append(version.document_id)
            matched.tokens = matched.tokens | tokens
            document_groups[version.document_id] = matched.group_id
            continue

        cluster_key = wire_service or content_hash or normalize_url(version.canonical_url)
        group_id = _group_id(f"{cluster_key}:{version.document_id}")
        state = _GroupState(
            group_id=group_id,
            cluster_key=cluster_key,
            origin_url=version.canonical_url,
            publisher=version.publisher,
            tokens=tokens,
            wire_service=wire_service,
            independence_confidence="uncertain" if uncertain_match else "established",
        )
        if uncertain_match:
            uncertain = True
        states.append(state)
        document_groups[version.document_id] = group_id

    groups = [
        SourceGroup(
            group_id=state.group_id,
            job_id=job_id,
            cluster_key=state.cluster_key[:255],
            origin_url=state.origin_url,
            publisher=state.publisher,
            member_count=state.member_count,
            independence_confidence=(
                "established" if state.independence_confidence == "established" else "uncertain"
            ),
        )
        for state in states
    ]
    return GroupingResult(
        document_groups=document_groups,
        groups=groups,
        uncertain_independence=uncertain,
    )


def apply_support_states(claims: list[Claim], groups: list[SourceGroup]) -> list[Claim]:
    """Assign support state without counting syndicated copies as corroboration."""

    group_sizes = {group.group_id: group.member_count for group in groups}
    by_stance: dict[tuple[str, ClaimStance], set[str]] = {}
    for claim in claims:
        key = (claim.predicate, claim.stance_on_proposition)
        by_stance.setdefault(key, set()).add(claim.independence_group_id)

    updated: list[Claim] = []
    for claim in claims:
        key = (claim.predicate, claim.stance_on_proposition)
        independent_groups = by_stance.get(key, set())
        if claim.support_state is SupportState.AUTHORITATIVE_PRIMARY_SOURCE:
            updated.append(claim)
            continue
        if len(independent_groups) >= 2:
            claim.support_state = SupportState.MULTIPLE_INDEPENDENT_SOURCES
        else:
            # A group with several members is still one reporting chain.
            claim.support_state = SupportState.SINGLE_SOURCE
        updated.append(claim)
    return updated


def syndication_ratio(groups: list[SourceGroup]) -> float:
    total = sum(group.member_count for group in groups)
    if total <= 0:
        return 0.0
    independent = len(groups)
    return max(0.0, 1.0 - (independent / total))
