"""Signed synthetic source HTTP -> actual ingress transactions. No real services."""

import base64
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.infra.digital_workers.mattermost_source import (
    MAX_RESPONSE_BYTES,
    REQUEST_DOMAIN,
    RESPONSE_DOMAIN,
    SOURCE_URL,
    OfflineMattermostSourceReader,
    SourceTarget,
    SourceUnavailable,
)
from tests.integration.test_mattermost_ingress import (
    PATH,
    cancellation,
    count,
    send,
)
from tests.integration.test_mattermost_ingress import (
    ingress as ingress,
)
from tests.integration.test_scene_execution_offline import scene_runtime as scene_runtime


@pytest.fixture
def adapter(ingress):
    e = ingress
    key = Ed25519PrivateKey.generate()
    mapping = e.repo.mapping("fixture-broker")
    target = SourceTarget(
        broker_id="fixture-broker",
        mapping_revision=mapping["revision"],
        binding=json.loads(mapping["binding_json"]),
        key_id="source-fixture-key",
        public_key=key.public_key().public_bytes_raw(),
    )
    state = {
        "fault": "",
        "requests": [],
        "resolved": 0,
        "signed": 0,
        "cached": None,
        "target": target,
    }

    async def resolve(expected):
        state["resolved"] += 1
        if e.repo.mapping(expected["broker_id"]) != expected or state["fault"] == "revoked_before":
            raise SourceUnavailable()
        if state["fault"] == "revoked_after" and state["requests"]:
            raise SourceUnavailable()
        if state["fault"] == "late_rewind" and state["requests"]:
            e.state["now"] -= 1
        if state["fault"] == "rotate_after" and state["requests"]:
            return state["target"].model_copy(update={"key_id": "rotated-source"})
        return state["target"]

    async def sign(scope, message):
        state["signed"] += 1
        if state["fault"] == "stale_signer":
            e.state["now"] += 5
        assert scope.broker_id == "fixture-broker"
        if state["fault"] == "signer_failure":
            raise RuntimeError("SECRET signer detail")
        signer = Ed25519PrivateKey.generate() if state["fault"] == "wrong_signer" else e.broker_key
        return signer.sign(message)

    async def endpoint(request):
        state["requests"].append(request)
        assert request.method == "POST" and str(request.url) == SOURCE_URL
        assert "authorization" not in request.headers
        assert request.headers["x-octop-source-broker"] == "fixture-broker"
        e.broker_key.public_key().verify(
            base64.b64decode(request.headers["x-octop-source-signature"]),
            REQUEST_DOMAIN + request.content,
        )
        asked = json.loads(request.content)
        assert asked["receipt"] == e.claims["intent"]["receipt"]
        assert asked["binding"] == e.claims["intent"]["binding"]
        assert set(asked) == {
            "protocol_version",
            "broker_id",
            "nonce",
            "issued_at",
            "binding",
            "receipt",
        }
        fault = state["fault"]
        if fault.startswith("http_"):
            return httpx.Response(
                int(fault[5:]),
                headers={"Location": "https://must-not-follow.invalid"},
                content=b"SECRET error",
            )
        if fault == "timeout":
            raise httpx.ReadTimeout("SECRET endpoint", request=request)
        if fault == "too_large":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b" " * (MAX_RESPONSE_BYTES + 1),
            )
        if fault == "replay":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, content=state["cached"]
            )
        c = e.state["candidate"]
        payload = {
            "protocol_version": 1,
            "request_sha256": hashlib.sha256(request.content).hexdigest(),
            "observed_at": e.state["now"],
            "expires_at": e.state["now"] + 5,
            "observation": {
                "delivery_key": c.delivery_key,
                "fingerprint": c.fingerprint,
                "commitment": c.source_commitment(),
                "state": e.state["source_state"],
                "receipt_expires_at": c.intent.receipt_expires_at,
                "version": str(e.state["version"]),
            },
        }
        if fault == "wrong_hash":
            payload["request_sha256"] = "f" * 64
        elif fault == "old":
            payload["observed_at"] -= 1
        elif fault == "future":
            payload["observed_at"] += 1
        elif fault == "expired":
            payload["expires_at"] = e.state["now"]
        elif fault == "long_ttl":
            payload["expires_at"] += 1
        elif fault == "wrong_scope":
            payload["observation"]["fingerprint"] = "f" * 64
        elif fault == "numeric_version":
            payload["observation"]["version"] = 1
        elif fault == "overflow_version":
            payload["observation"]["version"] = "9223372036854775808"
        elif fault == "extra":
            payload["goal"] = "SECRET untrusted text"
        elif fault == "bool_version":
            payload["protocol_version"] = True
        raw = json.dumps(payload, separators=(",", ":")).encode()
        if fault == "duplicate":
            raw = raw[:-1] + b',"protocol_version":1}'
        signer = Ed25519PrivateKey.generate() if fault == "wrong_key" else key
        domain = b"wrong-domain" if fault == "wrong_domain" else RESPONSE_DOMAIN
        envelope = {
            "key_id": "unknown" if fault == "wrong_key_id" else target.key_id,
            "payload": base64.b64encode(raw).decode(),
            "signature": base64.b64encode(signer.sign(domain + raw)).decode(),
        }
        if fault == "tamper":
            envelope["payload"] = base64.b64encode(raw + b" ").decode()
        content = json.dumps(envelope).encode()
        state["cached"] = content
        if fault == "rewind":
            e.state["now"] -= 1
        if fault == "local_revoke":
            with e.db.transaction() as conn:
                conn.execute("UPDATE mattermost_brokers SET enabled=0")
        headers = {"content-type": "text/plain" if fault == "content_type" else "application/json"}
        if fault == "compressed":
            headers["content-encoding"] = "unexpected"
        return httpx.Response(200, headers=headers, content=content)

    transport = httpx.MockTransport(endpoint)
    reader = OfflineMattermostSourceReader(
        transport=transport, resolve=resolve, sign=sign, clock=lambda: e.state["now"]
    )
    e.service.source = reader
    return SimpleNamespace(env=e, state=state, reader=reader, resolve=resolve, sign=sign)


@pytest.mark.asyncio
async def test_signed_source_drives_intake_and_cancellation(adapter):
    e, state = adapter.env, adapter.state
    assert (await send(e)).status_code == 201
    assert (await send(e)).status_code == 200
    assert len(state["requests"]) == 2
    nonces = [json.loads(r.content)["nonce"] for r in state["requests"]]
    assert nonces[0] != nonces[1]
    e.state.update(source_state="cancelled", version=2)
    path, body, headers = cancellation(e)
    result = await send(e, "POST", path, body, headers)
    assert result.status_code == 200 and result.json()["state"] == "cancelled"
    assert (await send(e, "GET", PATH + "/" + e.key)).json()["source_version"] == "2"
    assert len(state["requests"]) == 3  # GET reconciliation performs no source I/O.
    assert count(e, "enterprise_scene_runs") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "http_302",
        "http_401",
        "http_403",
        "http_404",
        "http_429",
        "http_503",
        "timeout",
        "too_large",
        "wrong_hash",
        "old",
        "future",
        "expired",
        "long_ttl",
        "numeric_version",
        "overflow_version",
        "extra",
        "bool_version",
        "duplicate",
        "wrong_key",
        "wrong_domain",
        "wrong_key_id",
        "tamper",
        "content_type",
        "compressed",
        "rewind",
        "local_revoke",
        "revoked_after",
        "late_rewind",
        "rotate_after",
    ],
)
async def test_untrusted_or_unavailable_source_never_persists(adapter, fault):
    e, state = adapter.env, adapter.state
    state["fault"] = fault
    result = await send(e)
    assert result.status_code == 503 and "SECRET" not in result.text
    assert count(e, "mattermost_intakes") == 0
    assert len(state["requests"]) == 1  # No redirects or retry.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["revoked_before", "signer_failure", "wrong_signer", "stale_signer"]
)
async def test_scope_or_signer_failure_prevents_dispatch(adapter, fault):
    adapter.state["fault"] = fault
    assert (await send(adapter.env)).status_code == 503
    assert not adapter.state["requests"]


@pytest.mark.asyncio
async def test_signed_scope_mismatch_and_cached_response_cannot_authorize(adapter):
    e, state = adapter.env, adapter.state
    state["fault"] = "wrong_scope"
    assert (await send(e)).status_code == 403
    state["fault"] = ""
    assert (await send(e)).status_code == 201
    state["fault"] = "replay"
    assert (await send(e)).status_code == 503
    assert count(e, "mattermost_intakes") == 1


@pytest.mark.asyncio
async def test_live_transport_and_reused_source_key_are_rejected_before_dispatch(adapter):
    class Subclass(httpx.MockTransport):
        pass

    live = httpx.AsyncHTTPTransport()
    try:
        for transport in (None, live, Subclass(lambda r: httpx.Response(200))):
            with pytest.raises(SourceUnavailable):
                OfflineMattermostSourceReader(
                    transport=transport,
                    resolve=adapter.resolve,
                    sign=adapter.sign,
                    clock=lambda: 101,
                )
    finally:
        await live.aclose()
    adapter.state["target"] = adapter.state["target"].model_copy(
        update={"public_key": adapter.env.broker_key.public_key().public_bytes_raw()}
    )
    assert (await send(adapter.env)).status_code == 503
    assert adapter.state["signed"] == 0 and not adapter.state["requests"]
