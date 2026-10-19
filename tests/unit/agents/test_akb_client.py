"""Offline contract tests for the AKB external API adapter."""
import asyncio
import json
from unittest.mock import patch

import pytest

from octop.infra.knowledge.akb_client import AkbClient, AkbConfig, AkbError


def test_akb_rejects_unsafe_endpoints_and_empty_credentials():
    for url in ("http://akb.example", "https://user:pass@akb.example", "https://akb.example/path"):
        with pytest.raises(ValueError):
            AkbConfig(url, "secret", 1)
    with pytest.raises(ValueError):
        AkbConfig("https://akb.example", "", 1)


def test_akb_search_contract():
    client = AkbClient(AkbConfig("https://akb.example", "secret", 12))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, n):
            return json.dumps({"success": True, "data": {"hits": []}, "traceId": "trace-1"}).encode()

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://akb.example/api/v1/external/search"
            assert request.get_header("X-api-key") == "secret"
            body = json.loads(request.data)
            assert body["knowledgeBaseId"] == 12
            assert body["permissionTags"] == ["ops"]
            return Response()

    with patch("urllib.request.build_opener", return_value=Opener()):
        result = asyncio.run(client.search("巡检", permission_tags=["ops"]))
    assert result["trace_id"] == "trace-1"
    assert result["knowledge_base_id"] == 12


def test_akb_rejects_invalid_response():
    client = AkbClient(AkbConfig("https://akb.example", "secret", 12))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, n):
            return b'{"success":false,"message":"private upstream error"}'

    class Opener:
        def open(self, request, timeout):
            return Response()

    with patch("urllib.request.build_opener", return_value=Opener()):
        with pytest.raises(AkbError, match="unsuccessful"):
            asyncio.run(client.query("问题"))
