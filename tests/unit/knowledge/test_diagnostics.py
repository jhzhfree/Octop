from octop.infra.knowledge.diagnostics import credential_rotation_state


def test_credential_expiry_and_rotation_reminders() -> None:
    now = 2_000_000_000
    assert (
        credential_rotation_state(has_credentials=False, expires_at=None, rotated_at=None, now=now)[
            "state"
        ]
        == "missing"
    )
    assert (
        credential_rotation_state(
            has_credentials=True, expires_at=now - 1, rotated_at=now, now=now
        )["state"]
        == "expired"
    )
    assert (
        credential_rotation_state(
            has_credentials=True, expires_at=now + 86400, rotated_at=now, now=now
        )["state"]
        == "expires_soon"
    )
    assert (
        credential_rotation_state(
            has_credentials=True, expires_at=None, rotated_at=now - 91 * 86400, now=now
        )["state"]
        == "rotation_due"
    )
    assert (
        credential_rotation_state(
            has_credentials=True, expires_at=None, rotated_at=now - 2 * 86400, now=now
        )["state"]
        == "current"
    )
