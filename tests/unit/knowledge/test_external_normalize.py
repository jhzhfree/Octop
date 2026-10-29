"""Normalizing upstream payloads: provenance has to survive every channel."""

from __future__ import annotations

from octop.infra.knowledge.external.normalize import chunk_from_mapping


def test_source_prefers_the_top_level_field() -> None:
    chunk = chunk_from_mapping(
        {"content": "x", "source": "交付规范.docx", "metadata": {"sourceName": "nested"}},
        origin="akb-rest",
    )
    assert chunk is not None
    assert chunk.source == "交付规范.docx"


def test_source_falls_back_to_nested_metadata() -> None:
    # MCP candidates keep provenance inside metadata, so a top-level-only lookup
    # would leave every MCP chunk without the document it came from.
    chunk = chunk_from_mapping(
        {"content": "x", "metadata": {"sourceName": "交付规范.docx"}},
        origin="akb-mcp",
    )
    assert chunk is not None
    assert chunk.source == "交付规范.docx"


def test_source_falls_back_to_title() -> None:
    chunk = chunk_from_mapping({"content": "x", "title": "周报"}, origin="akb-mcp")
    assert chunk is not None
    assert chunk.source == "周报"


def test_source_is_empty_when_upstream_has_none() -> None:
    chunk = chunk_from_mapping({"content": "x", "metadata": {}}, origin="akb-mcp")
    assert chunk is not None
    assert chunk.source == ""
