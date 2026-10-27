"""Unit tests for external knowledge normalization, fusion and URL guards."""

from __future__ import annotations

from octop.infra.knowledge.external.fuse import rrf_fuse
from octop.infra.knowledge.external.http import validate_endpoint
from octop.infra.knowledge.external.normalize import (
    coerce_score,
    dedupe_chunks,
    extract_items,
    filter_chunks,
    normalize_chunks,
)
from octop.infra.knowledge.external.types import ExternalSourceError


def test_coerce_score_normalizes_scales() -> None:
    assert coerce_score(0.42) == 0.42
    assert coerce_score(85) == 0.85
    assert coerce_score(-1) == 0.0
    # Values above 1 are folded down as percentage scales; out-of-range clips.
    assert coerce_score(5) == 0.05
    assert coerce_score(150) == 1.0
    assert coerce_score(None) == 0.0
    assert coerce_score("n/a") == 0.0


def test_normalize_chunks_maps_vendor_fields() -> None:
    raw = [
        {"content": "alpha", "score": 0.9, "documentName": "a.md", "id": "c1"},
        {"text": "beta", "similarity": "0.5", "source_uri": "b.md"},
        {"passage": "gamma", "distance": 0.25},
        {"unrelated": "skip"},
    ]
    chunks = normalize_chunks(raw, origin="akb", ref="KB")
    assert [chunk.content for chunk in chunks] == ["alpha", "beta", "gamma"]
    assert chunks[0].source == "a.md"
    assert chunks[0].score == 0.9
    assert chunks[1].score == 0.5
    # Distance is inverse to relevance.
    assert round(chunks[2].score, 2) == 0.75
    assert chunks[0].origin == "akb"
    assert chunks[0].ref == "KB"


def test_dedupe_and_filter_chunks() -> None:
    chunks = normalize_chunks(
        [
            {"id": "same", "content": "x", "score": 0.2},
            {"id": "same", "content": "x", "score": 0.8},
            {"id": "low", "content": "y", "score": 0.1},
        ],
        origin="standard",
    )
    deduped = dedupe_chunks(chunks)
    assert len(deduped) == 2
    assert [chunk.score for chunk in deduped] == [0.8, 0.1]
    assert [chunk.id for chunk in filter_chunks(deduped, min_score=0.5, limit=5)] == ["same"]


def test_extract_items_unwraps_envelopes() -> None:
    assert len(extract_items({"chunks": [{"content": "a"}]})) == 1
    assert len(extract_items({"data": {"results": [{"content": "a"}]}})) == 1
    assert extract_items("nope") == []


def test_rrf_fuse_rewards_overlap() -> None:
    chunks = normalize_chunks(
        [
            {"id": "shared", "content": "s", "score": 0.1},
            {"id": "only-a", "content": "a", "score": 0.9},
            {"id": "only-b", "content": "b", "score": 0.9},
        ],
        origin="x",
    )
    by_id = {chunk.id: chunk for chunk in chunks}
    fused = rrf_fuse(
        [[by_id["only-a"], by_id["shared"]], [by_id["only-b"], by_id["shared"]]],
        limit=3,
        key=lambda chunk: chunk.dedupe_key(),
    )
    assert [chunk.id for chunk in fused][0] == "shared"


def test_validate_endpoint_rejects_credentials_and_plain_http() -> None:
    assert validate_endpoint("https://akb.example.com/") == "https://akb.example.com"
    for bad in (
        "ftp://host",
        "https://user:pw@host",
        "https://host?a=1",
        "https://host#frag",
        "https:///nohost",
    ):
        try:
            validate_endpoint(bad)
        except ExternalSourceError:
            continue
        raise AssertionError(f"expected rejection: {bad}")
    try:
        validate_endpoint("http://192.168.1.10:8080")
    except ExternalSourceError as exc:
        assert exc.code == "INVALID_ARGUMENT"
    else:
        raise AssertionError("plain http must be rejected by default")
    assert validate_endpoint("http://192.168.1.10:8080", allow_insecure_http=True).startswith(
        "http://"
    )
