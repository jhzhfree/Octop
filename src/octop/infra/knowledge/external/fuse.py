"""Reciprocal-rank fusion across heterogeneous retrieval sources."""

from __future__ import annotations

from collections.abc import Callable, Sequence

_RRF_K = 60


def rrf_fuse[T](
    groups: Sequence[Sequence[T]],
    *,
    limit: int,
    key: Callable[[T], str],
    k: int = _RRF_K,
) -> list[T]:
    """Fuse ranked lists whose scores are not directly comparable.

    Local vector scores and external relevance scores use different scales, so
    they are combined by rank instead of by value: ``score += 1 / (k + rank)``.
    Items appearing in several lists accumulate weight and rise to the top.
    """
    if limit <= 0:
        return []
    scored: dict[str, float] = {}
    first_seen: dict[str, T] = {}
    for group in groups:
        for rank, item in enumerate(group, start=1):
            item_key = key(item)
            scored[item_key] = scored.get(item_key, 0.0) + 1.0 / (k + rank)
            if item_key not in first_seen:
                first_seen[item_key] = item
    ordered = sorted(scored.items(), key=lambda pair: pair[1], reverse=True)
    return [first_seen[item_key] for item_key, _ in ordered[:limit]]
