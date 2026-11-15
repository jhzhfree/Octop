"""Local deployment-only Mattermost metadata provisioning. Never resolves secrets."""

from __future__ import annotations

import json
from pathlib import Path

import click


@click.command("mattermost-instance")
@click.option(
    "--manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
)
@click.option(
    "--actor-id", type=int, required=True, help="Existing enabled platform administrator ID."
)
@click.option("--expected-revision", type=click.IntRange(min=0), required=True)
def mattermost_instance(manifest: Path, actor_id: int, expected_revision: int) -> None:
    """Provision/revoke a trusted alias from a non-secret manifest (offline DB)."""
    from octop.cli.support.db import open_cli_services, resolve_cli_locale
    from octop.i18n import tr
    from octop.infra.digital_workers.mattermost_instances import (
        InstanceConfiguration,
        configure_instance,
    )

    try:
        # CLI trusts host filesystem access, not a remotely asserted actor ID.
        with manifest.open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError
        configuration = InstanceConfiguration.model_validate_json(raw)
        with open_cli_services() as services:
            revision = configure_instance(
                services.repos,
                actor_id=actor_id,
                expected_revision=expected_revision,
                configuration=configuration,
            )
        if revision is None:
            raise ValueError
    except Exception:
        # No raw manifest, paths, credential refs, SQL errors or exception repr.
        raise click.ClickException(
            tr("mattermost.configuration_rejected", resolve_cli_locale())
        ) from None
    click.echo(
        json.dumps({"revision": revision, "status": configuration.status, "connected": False})
    )
