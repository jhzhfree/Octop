"""tests/unit/test_errors.py"""

from __future__ import annotations

import pytest

from octop.infra.errors import ErrorCode, OctopError


def test_octop_error_carries_code_and_status():
    err = OctopError(ErrorCode.AGENT_NOT_FOUND, "missing", details={"id": "a"})
    assert err.code is ErrorCode.AGENT_NOT_FOUND
    assert err.status == 404
    assert err.message == "missing"
    assert err.details == {"id": "a"}


def test_octop_error_default_status_for_known_code():
    err = OctopError(ErrorCode.FORBIDDEN, "nope")
    assert err.status == 403


def test_octop_error_to_envelope():
    err = OctopError(ErrorCode.AUTH_FAILED, "bad creds")
    assert err.to_envelope() == {
        "error": {"code": "AUTH_FAILED", "message": "bad creds", "details": {}}
    }


def test_octop_error_to_envelope_localized():
    err = OctopError(ErrorCode.TOKEN_EXPIRED, "token expired")
    assert err.to_envelope(locale="zh")["error"]["message"] == "登录已过期，请重新登录。"


def test_unknown_error_code_rejected():
    with pytest.raises(ValueError):
        ErrorCode("NOT_A_CODE")


def test_details_never_shadow_message_parameters():
    """Details are spliced into the template, so reserved names must be dropped.

    A detail key called ``code`` used to collide with the message function's own
    parameter and turned every localized response into a 500.
    """
    err = OctopError(
        ErrorCode.AGENT_IDENTITY_UNAVAILABLE,
        "unavailable",
        details={"code": "BRIDGE", "reason": "bridge-error", "locale": "zh"},
    )
    envelope = err.to_envelope(locale="zh")
    assert envelope["error"]["details"]["code"] == "BRIDGE"
    assert envelope["error"]["message"] == err.localized_message("zh")
    assert err.localized_message("en")
