"""Explicit SQLite local-operator migration; no network or service activation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from octop.infra.digital_workers.mattermost_maintenance import IntakeError, maintain_broker


@click.group()
@click.option(
    "--database", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--broker", required=True)
@click.pass_context
def mattermost_ingress(ctx: click.Context, database: Path, broker: str) -> None:
    """Inspect legacy intake histories or suspend admission. Resume is blocked.

    Requires local database write authority. Does not mount a production receiver.
    """
    ctx.obj = {"database": database, "broker": broker}


def _run(ctx: click.Context, **kwargs: Any) -> None:
    try:
        result = maintain_broker(**ctx.obj, **kwargs)
    except Exception as exc:
        # A failed acknowledgement can follow a committed transaction. Do not
        # suggest a new identity or print database/credential-bearing exceptions.
        code = exc.code if isinstance(exc, IntakeError) else "MAINTENANCE_FAILED_CHECK_LOCAL_AUDIT"
        raise click.ClickException(code) from None
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@mattermost_ingress.command("plan")
@click.pass_context
def plan(ctx: click.Context) -> None:
    """Inspect retained histories; the digest does not authorize retirement or resume."""
    _run(ctx, action="plan")


@mattermost_ingress.command("retire-and-resume")
@click.option("--plan-digest", required=True)
@click.option("--reason", required=True)
@click.pass_context
def resume(ctx: click.Context, plan_digest: str, reason: str) -> None:
    """Unavailable: old-process isolation cannot be established. Always refuses."""
    _run(ctx, action="retire-and-resume", digest=plan_digest, reason=reason)


@mattermost_ingress.command("suspend")
@click.option("--revision", required=True, type=click.IntRange(min=1))
@click.option("--reason", required=True)
@click.pass_context
def suspend(ctx: click.Context, revision: int, reason: str) -> None:
    """Disable admission again, retaining every permanent fence and nonce."""
    _run(ctx, action="suspend", revision=revision, reason=reason)


@mattermost_ingress.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Inspect enabled/revision and the last 20 maintenance audit events."""
    _run(ctx, action="status")
