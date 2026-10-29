"""Unit tests for built-in knowledge-base LangChain tools."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.config import var_child_runnable_config

from octop.infra.knowledge.external.types import ExternalSourceSpec
from octop.infra.knowledge.tools import _merge_specs, build_knowledge_tools


@contextmanager
def _configurable(**kwargs: object):
    token = var_child_runnable_config.set({"configurable": kwargs})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


def _tool_by_name(tools: list, name: str):
    for tool in tools:
        if tool.name == name:
            return tool
    raise KeyError(name)


def test_search_knowledge_default_description_is_empty_catalog_fallback() -> None:
    tool = _tool_by_name(build_knowledge_tools(SimpleNamespace()), "search_knowledge")
    assert tool.description == "No knowledge bases are attached this turn."


@pytest.mark.asyncio
async def test_search_knowledge_requires_selected_bases() -> None:
    services = SimpleNamespace(
        knowledge_repo=MagicMock(),
        settings_repo=MagicMock(),
        provider_repo=MagicMock(),
    )
    tool = _tool_by_name(build_knowledge_tools(services), "search_knowledge")
    with _configurable(user="1", knowledge_base_ids=[]):
        out = await tool.ainvoke({"query": "billing policy"})
    assert "No knowledge bases selected" in out


@pytest.mark.asyncio
async def test_search_knowledge_returns_retrieved_context() -> None:
    services = SimpleNamespace(
        knowledge_repo=MagicMock(),
        settings_repo=MagicMock(),
        provider_repo=MagicMock(),
    )
    tool = _tool_by_name(build_knowledge_tools(services), "search_knowledge")
    retrieved = AsyncMock(return_value="[KB: Docs]\nuseful passage")
    with (
        _configurable(
            user="7",
            user_is_admin=False,
            knowledge_base_ids=["kb-1"],
            locale="zh",
        ),
        patch("octop.infra.knowledge.tools.retrieve_context", retrieved),
    ):
        out = await tool.ainvoke({"query": "refund rules", "k": 3})

    assert out == "[KB: Docs]\nuseful passage"
    retrieved.assert_awaited_once()
    kwargs = retrieved.await_args.kwargs
    assert kwargs["user_id"] == 7
    assert kwargs["is_admin"] is False
    assert kwargs["query"] == "refund rules"
    assert kwargs["knowledge_base_ids"] == ["kb-1"]
    assert kwargs["k"] == 3
    assert kwargs["locale"] == "zh"


@pytest.mark.asyncio
async def test_search_knowledge_empty_hits_message() -> None:
    services = SimpleNamespace(
        knowledge_repo=MagicMock(),
        settings_repo=MagicMock(),
        provider_repo=MagicMock(),
    )
    tool = _tool_by_name(build_knowledge_tools(services), "search_knowledge")
    with (
        _configurable(user="1", knowledge_base_ids=["kb-1"]),
        patch(
            "octop.infra.knowledge.tools.retrieve_context",
            AsyncMock(return_value=""),
        ),
    ):
        out = await tool.ainvoke({"query": "nothing"})
    assert "No relevant passages found" in out


def _external_services() -> SimpleNamespace:
    """Services including the repos external knowledge needs.

    Mirrors what the agent manager wires up: leaving them out used to make
    every binding lookup degrade silently to "nothing mounted".
    """
    return SimpleNamespace(
        knowledge_repo=MagicMock(),
        settings_repo=MagicMock(),
        provider_repo=MagicMock(),
        external_knowledge_repo=MagicMock(),
        connector_repo=MagicMock(),
        secret_repo=MagicMock(),
    )


def _spec(binding_id: str) -> ExternalSourceSpec:
    return ExternalSourceSpec(
        binding_id=binding_id,
        display_name="AKB",
        kind="akb-mcp",
        endpoint="https://akb.example.com",
    )


@pytest.mark.asyncio
async def test_search_knowledge_includes_expert_mounted_sources() -> None:
    tool = _tool_by_name(build_knowledge_tools(_external_services()), "search_knowledge")
    retrieved = AsyncMock(return_value="passage")

    def only_agent_scope(_services: Any, target_type: str, _ids: Any) -> list[Any]:
        return [_spec("src-1")] if target_type == "agent" else []

    with (
        _configurable(user="7", knowledge_base_ids=["kb-1"], agent_id="agent-1"),
        patch("octop.infra.knowledge.tools.retrieve_context", retrieved),
        patch("octop.infra.knowledge.tools.external_specs_for_targets", only_agent_scope),
    ):
        await tool.ainvoke({"query": "q"})

    specs = retrieved.await_args.kwargs["external_specs"]
    assert [spec.binding_id for spec in specs] == ["src-1"]


@pytest.mark.asyncio
async def test_search_knowledge_runs_on_external_sources_only() -> None:
    """An expert may mount external sources and select no internal base."""
    tool = _tool_by_name(build_knowledge_tools(_external_services()), "search_knowledge")
    retrieved = AsyncMock(return_value="passage")
    with (
        _configurable(user="7", knowledge_base_ids=[], agent_id="agent-1"),
        patch("octop.infra.knowledge.tools.retrieve_context", retrieved),
        patch(
            "octop.infra.knowledge.tools.external_specs_for_targets",
            lambda _s, _t, _i: [_spec("src-1")],
        ),
    ):
        out = await tool.ainvoke({"query": "q"})

    assert out == "passage"
    assert retrieved.await_args.kwargs["knowledge_base_ids"] == []


def test_merge_specs_dedupes_by_source() -> None:
    # One source mounted on both the expert and a selected base must be
    # queried once, not fused against itself.
    assert len(_merge_specs([_spec("src-1")], [_spec("src-1")])) == 1
    assert len(_merge_specs([_spec("src-1")], [_spec("src-2")])) == 2
