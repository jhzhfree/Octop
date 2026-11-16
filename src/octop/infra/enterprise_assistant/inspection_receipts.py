"""Offline adapter conformance boundary; no transport, credentials or execution authority.

A matching receipt proves correlation only, never authentication or authorization.
Do not attach this preview to scene acceptance as verified execution evidence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .adapters import WorkflowError
from .reports import format_report

Reference = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MAX_RECEIPT_BYTES = 65536


class ReceiptContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class InspectionBinding(ReceiptContract):
    """Expected values supplied by a future trusted server policy resolver, not a tool."""

    organization_id: Reference
    worker_id: Reference
    agent_id: Reference
    run_id: Reference
    request_id: Reference  # Unique per attempt; persistent replay protection is still required.
    connector_id: Reference
    connector_version: int = Field(ge=1)
    policy_version: int = Field(ge=1)
    principal_id: Reference  # External account reference, not a credential or display name.
    target_id: Reference
    operation_id: Reference  # Registered bounded operation; never arbitrary shell text.

    def fingerprint(self) -> str:
        raw = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class ReceiptExpectation(ReceiptContract):
    binding: InspectionBinding
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    # No "verified" option: a conforming payload does not prove real execution.
    provenance: Literal["simulation", "unverified_external"]

    @model_validator(mode="after")
    def bounded_window(self) -> ReceiptExpectation:
        if not 0 < self.expires_at - self.issued_at <= 300:
            raise ValueError("receipt window must be between 1 and 300 seconds")
        return self


class InspectionReceipt(ReceiptContract):
    schema_version: Literal[1]
    binding_sha256: Digest
    principal_id: Reference
    target_id: Reference
    operation_id: Reference
    audit_event_id: Reference
    started_at: int = Field(ge=0)
    finished_at: int = Field(ge=0)
    outcome: Literal["collected", "denied", "failed"]
    # References only. A hash is not proof that an artifact exists or is authentic.
    artifact_sha256: Digest | None = None
    artifact_bytes: int | None = Field(default=None, ge=0, le=1048576)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema version must be an integer")
        return value

    @model_validator(mode="after")
    def result_shape(self) -> InspectionReceipt:
        if self.finished_at < self.started_at:
            raise ValueError("invalid receipt interval")
        has_artifact = self.artifact_sha256 is not None and self.artifact_bytes is not None
        if self.outcome == "collected":
            if not has_artifact:
                raise ValueError("collected receipt requires artifact metadata")
        elif self.artifact_sha256 is not None or self.artifact_bytes is not None:
            raise ValueError("failed or denied receipt cannot declare collected evidence")
        return self


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def validate_receipt(raw: bytes, expected: ReceiptExpectation, *, now: int) -> InspectionReceipt:
    """Bounded, pure parsing and correlation; callers retain original bytes separately.

    No retry, remote invocation, signature verification, replay store or policy lookup.
    Error messages deliberately omit untrusted payloads and possible secrets.
    """
    if len(raw) > MAX_RECEIPT_BYTES:
        raise WorkflowError("INSPECTION_RECEIPT_TOO_LARGE")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
        receipt = InspectionReceipt.model_validate(value)
    except (ValueError, RecursionError):
        raise WorkflowError("INSPECTION_RECEIPT_INVALID") from None
    binding = expected.binding
    if (
        receipt.binding_sha256 != binding.fingerprint()
        or receipt.principal_id != binding.principal_id
        or receipt.target_id != binding.target_id
        or receipt.operation_id != binding.operation_id
    ):
        raise WorkflowError("INSPECTION_RECEIPT_SCOPE_MISMATCH")
    if (
        not expected.issued_at
        <= receipt.started_at
        <= receipt.finished_at
        <= now
        < expected.expires_at
    ):
        raise WorkflowError("INSPECTION_RECEIPT_WINDOW_INVALID")
    return receipt


def receipt_preview(raw: bytes, expected: ReceiptExpectation, *, now: int, locale: str) -> str:
    """Use the existing report renderer, visibly outside verified scene evidence."""
    receipt = validate_receipt(raw, expected, now=now)
    binding = expected.binding
    return format_report(
        [
            {
                "kind": "inspection",
                "name": f"OFFLINE CONTRACT PREVIEW [{expected.provenance}]",
                "run_id": binding.run_id,
                "collected_at": now,
                "collection_status": "succeeded" if receipt.outcome == "collected" else "failed",
                "facts": [
                    {
                        "type": "health",
                        "status": "unknown" if receipt.outcome == "collected" else "unavailable",
                    },
                    {
                        "type": "receipt_contract",
                        "name": "Correlation only; identity, artifacts and execution NOT VERIFIED",
                        "value": json.dumps(
                            {"binding": binding.model_dump(), "receipt": receipt.model_dump()},
                            sort_keys=True,
                        ),
                    },
                ],
            }
        ],
        locale,
    )
