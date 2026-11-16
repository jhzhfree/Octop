"""Offline contract tests: no transport or real commands, synthetic identity and receipts."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from octop.infra.enterprise_assistant.adapters import WorkflowError
from octop.infra.enterprise_assistant.inspection_receipts import (
    MAX_RECEIPT_BYTES,
    InspectionBinding,
    ReceiptExpectation,
    receipt_preview,
    validate_receipt,
)


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("receipt validation must not perform I/O")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)


@pytest.fixture
def expected():
    return ReceiptExpectation(
        binding=InspectionBinding(
            organization_id="org1",
            worker_id="worker1",
            agent_id="agent1",
            run_id="run1",
            request_id="attempt1",
            connector_id="connector1",
            connector_version=1,
            policy_version=2,
            principal_id="service-account1",
            target_id="machine1",
            operation_id="linux-summary-v1",
        ),
        issued_at=1000,
        expires_at=1060,
        provenance="simulation",
    )


def payload(expected, **updates):
    value = {
        "schema_version": 1,
        "binding_sha256": expected.binding.fingerprint(),
        "principal_id": expected.binding.principal_id,
        "target_id": expected.binding.target_id,
        "operation_id": expected.binding.operation_id,
        "audit_event_id": "event1",
        "started_at": 1001,
        "finished_at": 1002,
        "outcome": "collected",
        "artifact_sha256": "a" * 64,
        "artifact_bytes": 12,
    }
    value.update(updates)
    return json.dumps(value).encode()


def test_synthetic_receipt_preview_never_proves_health_or_execution(expected):
    raw = payload(expected)
    receipt = validate_receipt(raw, expected, now=1003)
    assert receipt.outcome == "collected"
    report = receipt_preview(raw, expected, now=1003, locale="en")
    assert "[simulation]" in report
    assert "NOT VERIFIED" in report
    for value in ("org1", "worker1", "agent1", "run1", "service-account1", "machine1", "event1"):
        assert value in report
    assert "unknown" in report.lower()
    assert "linux-summary-v1" in report


@pytest.mark.parametrize("field", list(InspectionBinding.model_fields))
def test_every_binding_dimension_rejects_cross_scope_and_stale_receipt(expected, field):
    values = expected.binding.model_dump()
    values[field] = (
        values[field] + 1 if isinstance(values[field], int) else values[field] + "-other"
    )
    current = ReceiptExpectation(
        binding=InspectionBinding.model_validate(values),
        issued_at=1000,
        expires_at=1060,
        provenance="simulation",
    )
    with pytest.raises(WorkflowError, match="SCOPE_MISMATCH"):
        validate_receipt(payload(expected), current, now=1003)


@pytest.mark.parametrize("field", ["principal_id", "target_id", "operation_id"])
def test_echoed_binding_hash_does_not_allow_identity_mismatch(expected, field):
    with pytest.raises(WorkflowError, match="SCOPE_MISMATCH"):
        validate_receipt(payload(expected, **{field: "other"}), expected, now=1003)


@pytest.mark.parametrize(
    "updates",
    [
        {"secret": "DO_NOT_RETAIN"},
        {"command": "arbitrary shell"},
        {"provenance": "verified"},
        {"artifact_bytes": True},
        {"artifact_bytes": 1048577},
        {"artifact_sha256": None},
        {"outcome": "healthy"},
        {"schema_version": 2},
        {"schema_version": True},
        {"started_at": "1001"},
        {"audit_event_id": "event\nforged"},
        {"finished_at": 999},
        {"outcome": "denied"},
        {"outcome": "failed"},
    ],
)
def test_malformed_or_overclaiming_receipt_rejected(expected, updates):
    with pytest.raises(WorkflowError, match="INSPECTION_RECEIPT_INVALID") as exc:
        validate_receipt(payload(expected, **updates), expected, now=1003)
    assert "DO_NOT_RETAIN" not in str(exc.value)


@pytest.mark.parametrize(
    "raw", [b"{", b"\xff", b"[]", b"null", b'{"schema_version":1,"schema_version":1}']
)
def test_invalid_encoding_json_and_duplicate_keys(expected, raw):
    with pytest.raises(WorkflowError, match="INVALID"):
        validate_receipt(raw, expected, now=1003)


def test_size_limit(expected):
    with pytest.raises(WorkflowError, match="TOO_LARGE"):
        validate_receipt(b" " * (MAX_RECEIPT_BYTES + 1), expected, now=1003)


@pytest.mark.parametrize(
    ("updates", "now"), [({}, 1060), ({}, 999), ({}, 1001), ({"started_at": 999}, 1003)]
)
def test_expired_future_and_pre_request_times(expected, updates, now):
    with pytest.raises(WorkflowError, match="WINDOW_INVALID"):
        validate_receipt(payload(expected, **updates), expected, now=now)


@pytest.mark.parametrize("outcome", ["denied", "failed"])
def test_failure_is_coverage_gap_without_fabricated_artifact(expected, outcome):
    raw = payload(expected, outcome=outcome, artifact_sha256=None, artifact_bytes=None)
    report = receipt_preview(raw, expected, now=1003, locale="en")
    assert "unavailable" in report.lower()
    assert f'"outcome": "{outcome}"' in report


def test_unverified_external_cannot_be_promoted_by_payload(expected):
    external = ReceiptExpectation(
        binding=expected.binding,
        issued_at=1000,
        expires_at=1060,
        provenance="unverified_external",
    )
    assert "[unverified_external]" in receipt_preview(
        payload(expected), external, now=1003, locale="en"
    )
    with pytest.raises(ValidationError):
        ReceiptExpectation(
            binding=expected.binding, issued_at=1000, expires_at=1060, provenance="verified"
        )
    with pytest.raises(ValidationError):
        ReceiptExpectation(
            binding=expected.binding, issued_at=1000, expires_at=1400, provenance="simulation"
        )


def test_concurrent_parsing_has_no_shared_scope_or_replay_authority(expected):
    raw = payload(expected)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: validate_receipt(raw, expected, now=1003), range(20)))
    assert all(item == results[0] for item in results)
    # Pure validation intentionally permits repeated parsing; a future execution adapter
    # must implement durable attempt claiming, not mistake this for replay protection.
    with pytest.raises(ValidationError):
        expected.binding.run_id = "changed"


def test_signature_verifies_exact_domain_separated_bytes_without_promoting_trust(expected):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from octop.infra.enterprise_assistant.inspection_receipts import (
        RECEIPT_SIGNATURE_DOMAIN,
        validate_signed_receipt,
    )

    # Ephemeral synthetic keys only; never read the application's secret store.
    key = Ed25519PrivateKey.generate()
    raw = payload(expected)
    public = key.public_key().public_bytes_raw()
    signature = key.sign(RECEIPT_SIGNATURE_DOMAIN + raw)
    result = validate_signed_receipt(
        raw, expected, now=1003, public_key=public, signature=signature
    )
    assert result == validate_receipt(raw, expected, now=1003)
    assert expected.provenance == "simulation"
    # Cryptographic validity is not durable replay prevention: parsing twice is allowed.
    assert (
        validate_signed_receipt(raw, expected, now=1003, public_key=public, signature=signature)
        == result
    )
    for candidate_raw, candidate_key, candidate_signature in [
        (raw + b" ", public, signature),
        (raw, Ed25519PrivateKey.generate().public_key().public_bytes_raw(), signature),
        (raw, public, key.sign(raw)),
        (raw, public, signature[:-1]),
        (raw, public[:-1], signature),
    ]:
        with pytest.raises(WorkflowError, match="SIGNATURE_INVALID"):
            validate_signed_receipt(
                candidate_raw,
                expected,
                now=1003,
                public_key=candidate_key,
                signature=candidate_signature,
            )
    # A valid signature cannot bypass correlation, expiry or parser strictness.
    for bad, now, code in [
        (payload(expected, principal_id="another-worker"), 1003, "SCOPE_MISMATCH"),
        (raw, 1060, "WINDOW_INVALID"),
        (payload(expected, credentials="forbidden"), 1003, "RECEIPT_INVALID"),
    ]:
        with pytest.raises(WorkflowError, match=code):
            validate_signed_receipt(
                bad,
                expected,
                now=now,
                public_key=public,
                signature=key.sign(RECEIPT_SIGNATURE_DOMAIN + bad),
            )
