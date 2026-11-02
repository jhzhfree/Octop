"""Independent AKB V2 identity SDK. No dependency on Octop."""

from .client import AkbError, AkbMcpClient, tool_payload
from .crypto import (
    KeyPair,
    generate_key_pair,
    generate_uuid,
    public_jwk,
    sign_data,
    validate_uuid,
    verify_signature,
)
from .proof import (
    HEADER,
    PROFILE,
    MemoryReplayStore,
    ReplayStore,
    create_request_proof,
    validate_binding,
    verify_request_proof,
)

__version__ = "0.1.0"
__all__ = [
    "HEADER",
    "PROFILE",
    "AkbError",
    "AkbMcpClient",
    "KeyPair",
    "MemoryReplayStore",
    "ReplayStore",
    "create_request_proof",
    "generate_key_pair",
    "generate_uuid",
    "public_jwk",
    "sign_data",
    "tool_payload",
    "validate_binding",
    "validate_uuid",
    "verify_request_proof",
    "verify_signature",
]
