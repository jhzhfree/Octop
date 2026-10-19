from __future__ import annotations

import json

from octop.infra.connectors import builder
from octop.infra.connectors.gateway.adapters import ragflow


def test_ragflow_catalog_and_credentials_are_product_specific() -> None:
    entry = builder.get_catalog_entry("ragflow")
    assert entry is not None
    payload = builder.validate_create_credentials(
        "ragflow", {"base_url": "https://ragflow.example.com", "api_key": "secret"}
    )
    assert payload["base_url"] == "https://ragflow.example.com/api/v1"
    assert payload["api_key"] == "secret"
    assert payload["internal_token"]


def test_ragflow_retrieval_is_limited_to_the_bound_dataset(monkeypatch) -> None:
    request: dict[str, object] = {}

    def fake_request(creds, method, path, *, body=None):
        request.update(method=method, path=path, body=body)
        return {
            "code": 0,
            "data": {
                "chunks": [
                    {
                        "content": "policy text",
                        "dataset_id": "approved-dataset",
                        "document_id": "doc-1",
                        "document_keyword": "policy.pdf",
                        "similarity": 0.92,
                    }
                ]
            },
        }

    monkeypatch.setattr(ragflow, "_request", fake_request)
    response = json.loads(
        ragflow.call_tool(
            {"base_url": "https://ragflow.example.com/api/v1", "api_key": "secret"},
            "retrieve",
            {"question": "leave policy", "dataset_id": "approved-dataset"},
        )
    )

    assert request["method"] == "POST"
    assert request["path"] == "/retrieval"
    assert request["body"] == {
        "question": "leave policy",
        "dataset_ids": ["approved-dataset"],
        "top_k": 8,
    }
    assert response["passages"][0]["document_name"] == "policy.pdf"


def test_ragflow_requires_bound_dataset_selector() -> None:
    try:
        ragflow.call_tool(
            {"base_url": "https://ragflow.example.com", "api_key": "x"},
            "retrieve",
            {"question": "q"},
        )
    except ValueError as exc:
        assert "approved dataset_id" in str(exc)
    else:
        raise AssertionError("retrieval must require a bound dataset")
