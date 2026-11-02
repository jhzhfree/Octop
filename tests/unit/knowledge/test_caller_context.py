from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import jwt
import pytest
from jwt.algorithms import RSAAlgorithm

from octop.infra.knowledge.external.caller import InvocationCaller, caller_scope, current_caller


@pytest.mark.asyncio
async def test_async_requests_isolate_callers_and_cleanup_after_failure():
    async def run(agent):
        caller = InvocationCaller("agent", "connection", "KB_A", "session", agent, agent_id=agent)
        with caller_scope(caller):
            await asyncio.sleep(0)
            assert current_caller().agent_id == agent
            with pytest.raises(FrozenInstanceError):
                caller.agent_id = "forged"
            return current_caller().describe()

    first, second = await asyncio.gather(run("a"), run("b"))
    assert first["agent_id"] == "a" and second["agent_id"] == "b"
    assert first["identity_source"] == "host-runtime"
    assert not {"private_key", "api_key", "service_key", "pem"}.intersection(first)
    with pytest.raises(RuntimeError):
        current_caller()
    with (
        pytest.raises(ValueError),
        caller_scope(InvocationCaller("service", "c", "KB_A", "s", "i")),
    ):
        raise ValueError("business failure")
    with pytest.raises(RuntimeError):
        current_caller()


def test_public_expired_vector_matches_node_and_java_without_disabling_live_verification():
    vector = json.loads(
        (Path(__file__).parents[2] / "fixtures/identity/akb-v2-vector.json").read_text()
    )
    key = RSAAlgorithm.from_jwk(json.dumps(vector["publicJwk"]))
    claims = jwt.decode(
        vector["proof"],
        key,
        algorithms=["RS256"],
        audience=vector["binding"]["audience"],
        options={"verify_exp": False},
    )
    assert claims["ver"] == 2
    assert claims["request_binding"]["target"] == vector["target"]
    assert (
        claims["request_binding"]["body_sha256"]
        == hashlib.sha256(vector["body"].encode()).hexdigest()
        == vector["bodySha256"]
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(
            vector["proof"], key, algorithms=["RS256"], audience=vector["binding"]["audience"]
        )
