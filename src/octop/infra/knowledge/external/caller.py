"""Secret-free host caller context, separate from request_auth's private signing state.

This is a trusted host assertion, not a claim that AKB has already accepted the
request. Remote identity/authorization is established by the upstream response.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class InvocationCaller:
    actor_type: Literal["service", "agent"]
    connection_id: str
    resource_id: str
    session_id: str
    invocation_id: str
    trace_id: str = ""
    host_agent_id: str = ""
    agent_id: str = ""
    runtime_agent: str = ""
    identity_source: str = "host-runtime"

    def describe(self) -> dict[str, str]:
        return asdict(self)


_CURRENT: ContextVar[InvocationCaller | None] = ContextVar(
    "external_knowledge_caller", default=None
)


def current_caller() -> InvocationCaller:
    caller = _CURRENT.get()
    if caller is None:
        raise RuntimeError("No external knowledge caller in this operation")
    return caller


@contextmanager
def caller_scope(caller: InvocationCaller) -> Iterator[InvocationCaller]:
    token = _CURRENT.set(caller)
    try:
        yield caller
    finally:
        _CURRENT.reset(token)
