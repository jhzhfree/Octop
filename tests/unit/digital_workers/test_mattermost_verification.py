"""All requests terminate in MockTransport; no instance or PAT is used."""

import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from octop.infra.digital_workers.mattermost_verification import (
    TrustedMattermostTarget,
    VerificationScope,
    observe_bot_identity,
)

TOKEN = "offline-test-credential-not-a-real-token"


def target(**changes):
    return TrustedMattermostTarget(
        scope=VerificationScope(
            organization_id="o",
            worker_id="w",
            actor_id=1,
            worker_version=1,
            intent_revision=1,
            configuration_revision=1,
            instance_key="mm",
        ),
        bot_id="b" * 26,
        token=SecretStr(TOKEN),
        **{"origin": "https://8.8.8.8", **changes},
    )


async def current(scope):
    return scope.organization_id == "o"


def profile(**changes):
    return {"id": "b" * 26, "is_bot": True, "delete_at": 0, "roles": "system_user", **changes}


@pytest.mark.parametrize(
    "origin",
    [
        "http://8.8.8.8",
        "https://example.com",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://10.0.0.1",
        "https://0.0.0.0",
        "https://224.0.0.1",
        "https://[::1]",
        "https://[::ffff:127.0.0.1]",
        "https://[fe80::1%25eth0]",
        "https://2130706433",
        "https://0177.0.0.1",
        "https://user:password@8.8.8.8",
        "https://8.8.8.8/",
        "https://8.8.8.8?x=1",
        "https://8.8.8.8#x",
        "https://8.8.8.8:0",
        "https://8.8.8.8\\@evil.example",
        "https://8.8.8.8\n",
    ],
)
def test_only_canonical_global_ip_origin(origin):
    with pytest.raises(ValidationError):
        target(origin=origin)


def test_secret_not_serialized_or_represented():
    config = target()
    assert TOKEN not in repr(config)
    assert TOKEN not in config.model_dump_json()


@pytest.mark.asyncio
async def test_success_is_observation_not_permission(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-use.invalid:3128")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://8.8.8.8/api/v4/users/me"
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert not request.content
        return httpx.Response(200, json=profile(email=TOKEN))

    result = await observe_bot_identity(
        target(), scope_is_current=current, transport=httpx.MockTransport(handler)
    )
    assert len(calls) == 1
    assert result.result == "bot_identity_observed"
    assert not result.execution_authorized
    assert not result.human_initiated_send_allowed
    assert not result.automatic_send_allowed
    assert TOKEN not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"is_bot": "true"},
        {"is_bot": 1},
        {"is_bot": False},
        {"is_bot": None},
        {"id": "a" * 26},
        {"delete_at": 1},
        {"delete_at": False},
        {"delete_at": "0"},
        {"delete_at": None},
        {"roles": "system_user system_admin"},
        {"roles": None},
    ],
)
async def test_reject_non_bot_disabled_mismatch_or_elevated(changes):
    result = await observe_bot_identity(
        target(),
        scope_is_current=current,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=profile(**changes))),
    )
    assert result.result == "identity_rejected"


@pytest.mark.asyncio
async def test_no_redirect_or_upstream_error_reflection():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.invalid/"}, text=TOKEN)

    result = await observe_bot_identity(
        target(), scope_is_current=current, transport=httpx.MockTransport(handler)
    )
    assert len(requests) == 1
    assert result.result == "identity_rejected"
    assert TOKEN not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not json", b"x" * 16385, b"[]"])
async def test_invalid_or_large_response(content):
    result = await observe_bot_identity(
        target(),
        scope_is_current=current,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=content, headers={"content-type": "application/json"}
            )
        ),
    )
    assert result.result in {"unavailable", "identity_rejected"}


@pytest.mark.asyncio
async def test_recheck_revocation_before_and_after_network():
    for allowed in ([False], [True, False]):
        checks = iter(allowed)
        requests = []

        async def check(scope, checks=checks):
            assert scope == target().scope
            return next(checks)

        def handler(request, requests=requests):
            requests.append(request)
            return httpx.Response(200, json=profile())

        result = await observe_bot_identity(
            target(), scope_is_current=check, transport=httpx.MockTransport(handler)
        )
        assert result.result == "access_changed"
        assert len(requests) == len(allowed) - 1


@pytest.mark.asyncio
async def test_sanitizes_transport_and_callback_failures():
    def handler(request):
        raise httpx.ConnectError(TOKEN, request=request)

    async def failed_check(scope):
        raise ValueError(TOKEN)

    for check in (current, failed_check):
        result = await observe_bot_identity(
            target(), scope_is_current=check, transport=httpx.MockTransport(handler)
        )
        assert result.result == "unavailable"
        assert TOKEN not in json.dumps(result.model_dump())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "text/html"},
        {"content-type": "application/json", "content-encoding": "gzip"},
    ],
)
async def test_reject_wrong_content_type_and_compressed_body_without_reading(headers):
    class Unreadable(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("must reject without consuming response")
            yield b""  # pragma: no cover

    result = await observe_bot_identity(
        target(),
        scope_is_current=current,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers=headers, stream=Unreadable())
        ),
    )
    # Reading the stream would become unavailable, rather than this rejection.
    assert result.result == "identity_rejected"


@pytest.mark.asyncio
async def test_timeout_and_cross_organization_fail_closed():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout(TOKEN, request=request)

    result = await observe_bot_identity(
        target(), scope_is_current=current, transport=httpx.MockTransport(handler)
    )
    assert result.result == "unavailable"
    assert TOKEN not in result.model_dump_json()

    async def other_organization(scope):
        return scope.organization_id == "other"

    result = await observe_bot_identity(
        target(), scope_is_current=other_organization, transport=httpx.MockTransport(handler)
    )
    assert result.result == "access_changed"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_client_has_no_proxy_redirect_or_tls_bypass(monkeypatch):
    original = httpx.AsyncClient
    options = []

    def client(**kwargs):
        options.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    await observe_bot_identity(
        target(),
        scope_is_current=current,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=profile())),
    )
    assert len(options) == 1
    assert options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False
    assert options[0]["verify"] is True
    assert options[0]["timeout"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", 1, {}, None])
async def test_permission_callback_requires_literal_true(value):
    calls = []

    async def invalid(scope):
        return value

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=profile())

    result = await observe_bot_identity(
        target(),
        scope_is_current=invalid,
        transport=httpx.MockTransport(handler),
    )
    assert result.result == "access_changed"
    assert not calls
