"""Unambiguous mapping from normalized market outcomes to executable quotes.

Polymarket returns the same condition ID for both binary outcomes, so outcome
ID alone cannot distinguish YES from NO.  Token ID is the primary key; outcome
ID is only used when it uniquely identifies one quote, and normalized labels
are the final fallback.
"""

from __future__ import annotations

from collections.abc import Iterable

from prediction_ai.schemas.market import NormalizedOrderBook, OutcomeQuote


def find_outcome_quote(
    order_book: NormalizedOrderBook,
    *,
    token_id: str | None,
    outcome_id: str | None,
    fallback_labels: Iterable[str],
) -> OutcomeQuote | None:
    """Locate one outcome quote without relying on ambiguous platform IDs."""
    if token_id:
        match = next(
            (quote for quote in order_book.quotes if quote.token_id == token_id),
            None,
        )
        if match is not None:
            return match
    if outcome_id:
        matches = [
            quote for quote in order_book.quotes if quote.outcome_id == outcome_id
        ]
        if len(matches) == 1:
            return matches[0]
    normalized_labels = {
        label.strip().lower() for label in fallback_labels if label.strip()
    }
    for quote in order_book.quotes:
        if quote.label.strip().lower() in normalized_labels:
            return quote
    return None
