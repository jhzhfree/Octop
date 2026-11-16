"""Public Go fixtures, synthetic local signing, no services or credentials."""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from octop.infra.digital_workers.mattermost_proof import (
    DOMAIN,
    MAX_PROOF_BYTES,
    ProofPolicy,
    ProofRejected,
    verify_proof,
)

VECTORS = json.loads(
    (Path(__file__).parents[2] / "fixtures/mattermost_handoff_vectors.json").read_text()
)


def policy(value):
    return ProofPolicy(
        enabled=value["Enabled"],
        key_id=value["KeyID"],
        public_key=base64.b64decode(value["PublicKey"]),
        binding=value["Binding"],
        octop_actor_id=value["OctopActorID"],
        octop_organization_id=value["OctopOrganizationID"],
        octop_employee_id=value["OctopEmployeeID"],
    )


def wire(value):
    # Independent fixture encoder follows the documented Go struct representation.
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    for char in ("<", ">", "&", "\u2028", "\u2029"):
        encoded = encoded.replace(char, f"\\u{ord(char):04x}")
    return encoded.encode()


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("proof decoding must be offline")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["Name"])
def test_published_go_vectors(vector):
    raw = base64.b64decode(vector["Proof"])
    if not vector["Accepted"]:
        with pytest.raises((ProofRejected, ValidationError)):
            verify_proof(raw, policy(vector["Policy"]), now=vector["Now"])
        return
    result = verify_proof(raw, policy(vector["Policy"]), now=vector["Now"])
    expected = vector["Expected"]
    assert result.delivery_key == expected["DeliveryKey"]
    assert result.fingerprint == expected["Fingerprint"]
    assert result.intent.model_dump() == expected["Intent"]
    assert result.octop_actor_id == expected["OctopActorID"]
    assert result.octop_organization_id == expected["OctopOrganizationID"]
    assert result.octop_employee_id == expected["OctopEmployeeID"]
    assert (
        result.source_commitment()
        == hashlib.sha256(
            b"mattermost-source-observation/v1\x00" + wire(expected["Intent"])
        ).hexdigest()
    )


@pytest.fixture
def signed():
    vector = VECTORS[0]
    key = Ed25519PrivateKey.generate()
    trusted = policy(vector["Policy"]).model_copy(
        update={"public_key": key.public_key().public_bytes_raw()}
    )
    envelope = json.loads(base64.b64decode(vector["Proof"]))
    claims = json.loads(base64.b64decode(envelope["payload"]))

    def seal(value, domain=DOMAIN):
        payload = wire(value)
        return wire(
            {
                "payload": base64.b64encode(payload).decode(),
                "signature": base64.b64encode(key.sign(domain + payload)).decode(),
            }
        )

    return trusted, claims, seal


@pytest.mark.parametrize(
    "field", ["instance", "audience", "organization", "employee", "user", "team", "channel", "bot"]
)
def test_valid_signature_does_not_override_receiver_binding(signed, field):
    trusted, claims, seal = signed
    claims["intent"]["binding"][field] += "x"
    with pytest.raises(ProofRejected):
        verify_proof(seal(claims), trusted, now=101)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", True),
        ("version", 2.0),
        ("version", 1),
        ("issued", "100"),
        ("issued", 102),
        ("issued", 0),
        ("expires", 101),
        ("expires", 221),
        ("expires", 9223372036854775808),
        ("key_id", "unregistered"),
    ],
)
def test_claim_type_time_and_key_rejections(signed, field, value):
    trusted, claims, seal = signed
    claims[field] = value
    with pytest.raises(ProofRejected):
        verify_proof(seal(claims), trusted, now=101)


@pytest.mark.parametrize("mutation", ["mode", "expiry", "empty", "long", "unknown", "actor"])
def test_valid_signature_does_not_override_intent_constraints(signed, mutation):
    trusted, claims, seal = signed
    intent = claims["intent"]
    if mutation == "mode":
        intent["mode"] = "automatic"
    elif mutation == "expiry":
        intent["receipt_expires_at"] = 159999
    elif mutation == "empty":
        intent["goal"] = "\u3000"
    elif mutation == "long":
        intent["goal"] = "中" * 2001
    elif mutation == "unknown":
        intent["command"] = "do not run"
    else:
        intent["octop_actor_id"] = 1
    with pytest.raises(ProofRejected):
        verify_proof(seal(claims), trusted, now=101)


def test_rotation_resigning_and_local_mapping_change_keep_identity(signed):
    trusted, claims, seal = signed
    first = verify_proof(seal(claims), trusted, now=101)
    claims["issued"] = 101
    second = verify_proof(seal(claims), trusted, now=102)
    assert (first.delivery_key, first.fingerprint) == (second.delivery_key, second.fingerprint)
    mapped = trusted.model_copy(update={"octop_actor_id": 10})
    remapped = verify_proof(seal(claims), mapped, now=102)
    assert remapped.delivery_key == first.delivery_key and remapped.fingerprint != first.fingerprint
    rotated = trusted.model_copy(update={"key_id": "rotated"})
    claims["key_id"] = "rotated"
    third = verify_proof(seal(claims), rotated, now=102)
    assert (first.delivery_key, first.fingerprint) == (third.delivery_key, third.fingerprint)
    assert first.intent.goal not in repr(first)


def test_disabled_wrong_key_domain_and_jwt_never_authorize(signed):
    trusted, claims, seal = signed
    raw = seal(claims)
    cases = [
        (raw, trusted.model_copy(update={"enabled": False})),
        (
            raw,
            trusted.model_copy(
                update={"public_key": Ed25519PrivateKey.generate().public_key().public_bytes_raw()}
            ),
        ),
        (seal(claims, b"other-domain"), trusted),
        (b"Bearer header.payload.signature", trusted),
    ]
    for body, p in cases:
        with pytest.raises(ProofRejected) as exc:
            verify_proof(body, p, now=101)
        assert str(exc.value) == "MATTERMOST_PROOF_REJECTED"


def test_noncanonical_json_base64_and_bad_input_rejected(signed):
    trusted, claims, seal = signed
    raw = seal(claims)
    envelope = json.loads(raw)
    duplicate = raw[:-1] + b',"payload":' + json.dumps(envelope["payload"]).encode() + b"}"
    alternate = dict(envelope, payload=envelope["payload"] + "\n")
    for body in [
        raw + b"\n",
        wire(dict(reversed(list(envelope.items())))),
        duplicate,
        wire(alternate),
        b"\xff",
        b"null",
        b"[]",
        b"{",
        b" " * (MAX_PROOF_BYTES + 1),
    ]:
        with pytest.raises(ProofRejected):
            verify_proof(body, trusted, now=101)


def test_opaque_ids_preserve_bytes_and_go_whitespace_semantics(signed):
    trusted, claims, seal = signed
    binding = claims["intent"]["binding"]
    binding["organization"] = "\x1c"  # Python strip differs; Go does not treat this as space.
    trusted = ProofPolicy.model_validate(trusted.model_dump() | {"binding": binding})
    assert verify_proof(seal(claims), trusted, now=101).intent.binding.organization == "\x1c"
    claims["intent"]["goal"] = "\x1c"
    assert verify_proof(seal(claims), trusted, now=101).intent.goal == "\x1c"
