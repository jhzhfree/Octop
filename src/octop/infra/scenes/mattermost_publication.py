"""Local publication review facade. No credentials, remote calls or dispatch."""

from __future__ import annotations

import re
from typing import Any

from octop.infra.db.repos.scene_acceptance import AcceptanceStoreError
from octop.infra.db.services import RepoBundle
from octop.infra.scenes.service import SceneError


class MattermostPublicationService:
    def __init__(self, repos: RepoBundle, actor_id: int) -> None:
        self.repo = repos.mattermost_publication_repo
        self.actor_id = actor_id

    def _call(self, method: str, org: str, run_id: str, **kwargs: Any) -> dict[str, Any]:
        if type(self.actor_id) is not int or self.actor_id < 1:
            raise SceneError("PUBLICATION_FORBIDDEN", "publication access denied", 403)
        try:
            result: dict[str, Any] = getattr(self.repo, method)(
                org, run_id, self.actor_id, **kwargs
            )
            return result
        except AcceptanceStoreError as exc:
            status = {"not_found": 404, "forbidden": 403, "stale": 409, "conflict": 409}[exc.reason]
            raise SceneError(
                "PUBLICATION_" + exc.reason.upper(), "publication request rejected", status
            ) from None
        except Exception:
            # SQL/driver errors must not expose report contents, targets or SQL parameters.
            raise SceneError("PUBLICATION_UNAVAILABLE", "publication unavailable", 503) from None

    @staticmethod
    def _hash(value: str) -> None:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise SceneError("PUBLICATION_INVALID", "invalid publication request", 422)

    def preview(
        self,
        org: str,
        run_id: str,
        *,
        expected_report_sha256: str,
        expected_worker_version: int,
        expected_intent_revision: int,
        expected_configuration_revision: int,
    ) -> dict[str, Any]:
        self._hash(expected_report_sha256)
        versions = (
            expected_worker_version,
            expected_intent_revision,
            expected_configuration_revision,
        )
        if any(type(v) is not int or not 1 <= v <= 9223372036854775807 for v in versions):
            raise SceneError("PUBLICATION_INVALID", "invalid publication request", 422)
        return self._call(
            "preview",
            org,
            run_id,
            expected={
                "report_sha256": expected_report_sha256,
                "worker_version": expected_worker_version,
                "intent_revision": expected_intent_revision,
                "configuration_revision": expected_configuration_revision,
            },
        )

    def get(self, org: str, run_id: str) -> dict[str, Any]:
        return self._call("get", org, run_id)

    def approve(self, org: str, run_id: str, *, expected_publication_sha256: str) -> dict[str, Any]:
        self._hash(expected_publication_sha256)
        return self._call("decide", org, run_id, digest=expected_publication_sha256, revoke=False)

    def revoke(self, org: str, run_id: str, *, expected_publication_sha256: str) -> dict[str, Any]:
        self._hash(expected_publication_sha256)
        return self._call("decide", org, run_id, digest=expected_publication_sha256, revoke=True)
