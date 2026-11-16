"""Service health must acknowledge liveness, not merely return JSON."""

from __future__ import annotations

import httpx
import pytest

from octop.infra.setup.service import probe_health, probe_health_with_retry


@pytest.mark.parametrize("payload", [{"ok": False}, {}, [], None, {"ok": "true"}, {"ok": 1}])
def test_health_rejects_non_acknowledgement(monkeypatch, payload):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **kw: httpx.Response(
            200, json=payload, request=httpx.Request("GET", "http://127.0.0.1/api/health")
        ),
    )
    assert probe_health("127.0.0.1", 8088)[0] is False


def test_health_accepts_setup_liveness_without_claiming_readiness(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **kw: httpx.Response(
            200,
            json={"ok": True, "db": False, "agents_running": 0},
            request=httpx.Request("GET", "http://127.0.0.1/api/health"),
        ),
    )
    ok, detail = probe_health("127.0.0.1", 8088)
    assert ok is True
    assert "readiness not checked" in detail


@pytest.mark.parametrize("status,content", [(500, b'{"ok":true}'), (200, b"<html>setup</html>")])
def test_health_rejects_failed_or_non_json_response(monkeypatch, status, content):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **kw: httpx.Response(
            status, content=content, request=httpx.Request("GET", "http://127.0.0.1/api/health")
        ),
    )
    assert probe_health("127.0.0.1", 8088)[0] is False


def test_health_recovers_from_unreachable_startup(monkeypatch):
    attempts = []

    def get(*a, **kw):
        attempts.append(kw)
        if len(attempts) == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(
            200, json={"ok": True}, request=httpx.Request("GET", "http://127.0.0.1/api/health")
        )

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr("octop.infra.setup.service.time.sleep", lambda _: None)
    assert probe_health_with_retry("127.0.0.1", 8088, attempts=2)[0] is True
    assert len(attempts) == 2
    assert all(call["timeout"] == 3 for call in attempts)
