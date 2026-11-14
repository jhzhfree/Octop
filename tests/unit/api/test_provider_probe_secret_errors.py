"""Upstream exceptions must not appear in API errors or application logs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from octop.infra.agents.providers.probe import fetch_openai_compatible_models, probe_provider_row


async def test_chat_exception_is_not_echoed_or_logged(caplog):
    row = SimpleNamespace(
        name="remote",
        kind="openai",
        base_url=None,
        api_key="synthetic-secret",
        extra_json=None,
        get_models=lambda: [{"id": "m"}],
    )
    model = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=RuntimeError("synthetic-secret synthetic-nested-header"))
    )
    with patch("octop.infra.agents.providers.probe.build_probe_chat_model", return_value=model):
        result = await probe_provider_row(row, model_id="m")
    assert not result["ok"]
    assert "synthetic" not in str(result) + caplog.text


async def test_fetch_exception_is_not_echoed_or_logged(caplog):
    with patch(
        "httpx.AsyncClient.get",
        new=AsyncMock(side_effect=RuntimeError("synthetic-secret synthetic-header")),
    ):
        result = await fetch_openai_compatible_models(
            base_url="https://gateway.example/v1",
            api_key="synthetic-secret",
            extra_headers={"X-Key": "synthetic-header"},
        )
    assert not result["ok"]
    assert "synthetic" not in str(result) + caplog.text


async def test_fetch_http_body_does_not_echo_sensitive_headers(caplog):
    import httpx

    response = httpx.Response(
        401, text='{"nested":{"Authorization":"synthetic-header","api_key":"synthetic-secret"}}'
    )
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)):
        result = await fetch_openai_compatible_models(
            base_url="https://gateway.example/v1", api_key="synthetic-secret"
        )
    assert not result["ok"]
    assert "API key" in result["error"]
    assert "synthetic" not in str(result) + caplog.text
