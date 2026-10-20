"""Human-reviewed platform-memory candidates produced by virtual-worker runs."""

from __future__ import annotations

import json
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from octop.infra.digital_workers.service import DigitalWorkerError
from octop.infra.knowledge.akb_client import AkbMemoryClient, AkbMemoryConfig
from octop.infra.knowledge.sources import canonical_memory_sources
from octop.infra.utils.ssrf_guard import validate_https_url


class WorkerMemoryService:
    def __init__(self, repos: Any, actor: Any, connector_service: Any) -> None:
        self.repos = repos
        self.actor = actor
        self.connector_service = connector_service

    def _require_manager(self, organization_id: str) -> None:
        if getattr(self.actor, "is_admin", False):
            return
        member = self.repos.organization_repo.get_member(organization_id, self.actor.id)
        if member is None or member.get("role") not in {"owner", "manager"}:
            raise DigitalWorkerError("ORGANIZATION_FORBIDDEN", "organization manager required", 403)

    def list_candidates(
        self,
        organization_id: str,
        *,
        worker_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        self._require_manager(organization_id)
        if worker_id is not None:
            worker = self.repos.digital_worker_repo.get_worker(worker_id)
            if worker is None or worker["organization_id"] != organization_id:
                raise DigitalWorkerError(
                    "DIGITAL_WORKER_NOT_FOUND", "virtual worker not found", 404
                )
        return cast(
            list[dict[str, Any]],
            self.repos.digital_worker_repo.list_memory_candidates(
                organization_id, worker_id=worker_id, status=status, limit=limit
            ),
        )

    async def review_candidate(
        self,
        organization_id: str,
        candidate_id: str,
        *,
        approve: bool,
        review_note: str,
        content: str | None = None,
    ) -> dict[str, Any]:
        self._require_manager(organization_id)
        candidate = self.repos.digital_worker_repo.get_memory_candidate(candidate_id)
        if candidate is None or candidate["organization_id"] != organization_id:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_MEMORY_CANDIDATE_NOT_FOUND", "memory candidate not found", 404
            )
        if candidate["status"] == "written" or candidate["status"] == "rejected":
            raise DigitalWorkerError(
                "DIGITAL_WORKER_MEMORY_CANDIDATE_DECIDED",
                "memory candidate is already decided",
                409,
            )
        if int(candidate["created_by"]) == int(self.actor.id):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_MEMORY_SELF_REVIEW",
                "candidate author cannot review their own memory candidate",
                403,
            )
        run = self.repos.enterprise_scene_repo.get_run(str(candidate["source_run_id"]))
        if run is not None and run["status"] == "queued" and run.get("cron_id"):
            cron = self.repos.cron_repo.get(str(run["cron_id"]))
            if cron is not None and cron.last_status in {"ok", "error"}:
                self.repos.enterprise_scene_repo.sync_cron_status(
                    str(run["run_id"]),
                    cron_id=str(run["cron_id"]),
                    status="succeeded" if cron.last_status == "ok" else "failed",
                )
                run = self.repos.enterprise_scene_repo.get_run(str(candidate["source_run_id"]))
        if run is None or run["status"] != "succeeded":
            raise DigitalWorkerError(
                "DIGITAL_WORKER_MEMORY_SOURCE_RUN_INVALID",
                "memory candidates require a successful source run",
                409,
            )
        self._assert_run_provenance(candidate, run)
        text = (content if content is not None else str(candidate["content"])).strip()
        if approve and (not text or len(text) > 4000):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_MEMORY_CONTENT_INVALID",
                "approved memory content must contain 1-4000 characters",
                422,
            )
        note = review_note.strip()[:4000]
        if not approve:
            if not self.repos.digital_worker_repo.reject_memory_candidate(
                candidate_id, reviewer_id=int(self.actor.id), review_note=note
            ):
                raise DigitalWorkerError(
                    "DIGITAL_WORKER_MEMORY_CANDIDATE_DECIDED",
                    "memory candidate is no longer pending review",
                    409,
                )
            self.repos.audit_repo.write(
                actor=str(self.actor.id),
                action="digital_worker.memory_candidate_reject",
                target=candidate_id,
            )
            return cast(
                dict[str, Any],
                self.repos.digital_worker_repo.get_memory_candidate(candidate_id) or candidate,
            )

        self._assert_binding_current(candidate)
        if not self.repos.digital_worker_repo.claim_memory_candidate(
            candidate_id,
            reviewer_id=int(self.actor.id),
            review_note=note,
            content=text,
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_MEMORY_CANDIDATE_DECIDED",
                "memory candidate is already being reviewed",
                409,
            )
        claimed = self.repos.digital_worker_repo.get_memory_candidate(candidate_id)
        if claimed is None:
            raise DigitalWorkerError(
                "DIGITAL_WORKER_MEMORY_CANDIDATE_NOT_FOUND", "memory candidate not found", 404
            )
        try:
            requester = self.repos.user_repo.get(int(candidate["created_by"]))
            if requester is None or requester.disabled:
                raise DigitalWorkerError(
                    "DIGITAL_WORKER_MEMORY_CONNECTOR_UNAVAILABLE",
                    "candidate owner's AKB connector is unavailable",
                    409,
                )
            config = self.connector_service.custom_harness_config_for_user(
                int(candidate["created_by"]), str(candidate["mcp_server"])
            )
            client = _akb_memory_client(config)
            result = await client.capture(
                content=text,
                scope_type=str(candidate["scope_type"]),
                scope_code=candidate.get("scope_code"),
                source_ref=str(candidate["source_run_id"]),
                idempotency_key=f"octop:{candidate_id}",
                tags=["octop", "approved-task-result", f"worker:{candidate['worker_id']}"],
            )
            memory_code = str(result["memory_code"])
        except Exception as exc:
            self.repos.digital_worker_repo.finish_memory_candidate(
                candidate_id,
                status="failed",
                error_summary="AKB memory capture failed",
            )
            self.repos.audit_repo.write(
                actor=str(self.actor.id),
                action="digital_worker.memory_candidate_write_failed",
                target=candidate_id,
                payload=json.dumps({"error": "AKB memory capture failed"}),
            )
            if isinstance(exc, DigitalWorkerError):
                raise
            raise DigitalWorkerError(
                "DIGITAL_WORKER_MEMORY_WRITE_FAILED", "AKB memory capture failed", 502
            ) from None
        self.repos.digital_worker_repo.finish_memory_candidate(
            candidate_id, status="written", memory_code=memory_code
        )
        self.repos.audit_repo.write(
            actor=str(self.actor.id),
            action="digital_worker.memory_candidate_approve",
            target=candidate_id,
            payload=json.dumps({"memory_code": memory_code}, ensure_ascii=False),
        )
        return cast(
            dict[str, Any],
            self.repos.digital_worker_repo.get_memory_candidate(candidate_id) or claimed,
        )

    def _assert_binding_current(self, candidate: dict[str, Any]) -> None:
        worker = self.repos.digital_worker_repo.get_worker(str(candidate["worker_id"]))
        sources = canonical_memory_sources((worker or {}).get("capabilities", {}))
        expected = (
            str(candidate["provider"]),
            str(candidate["mcp_server"]),
            str(candidate["scope_type"]),
            str(candidate.get("scope_code") or ""),
        )
        if not any(
            (
                source.get("provider"),
                source.get("mcp_server"),
                source.get("scope_type"),
                str(source.get("scope_code") or ""),
            )
            == expected
            for source in sources
            if source.get("capture_enabled", True)
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_MEMORY_BINDING_CHANGED",
                "memory binding changed; review cannot write to the previous scope",
                409,
            )

    @staticmethod
    def _assert_run_provenance(candidate: dict[str, Any], run: dict[str, Any]) -> None:
        snapshot = run.get("plan_snapshot") or {}
        worker = snapshot.get("worker") or {}
        capabilities = snapshot.get("capabilities") or {}
        expected = (
            str(candidate["provider"]),
            str(candidate["mcp_server"]),
            str(candidate["scope_type"]),
            str(candidate.get("scope_code") or ""),
        )
        sources = canonical_memory_sources(capabilities)
        if (
            str(run.get("organization_id")) != str(candidate["organization_id"])
            or int(run.get("requested_by") or 0) != int(candidate["created_by"])
            or str(worker.get("worker_id") or "") != str(candidate["worker_id"])
            or not any(
                (
                    source.get("provider"),
                    source.get("mcp_server"),
                    source.get("scope_type"),
                    str(source.get("scope_code") or ""),
                )
                == expected
                for source in sources
                if source.get("capture_enabled", True)
            )
        ):
            raise DigitalWorkerError(
                "DIGITAL_WORKER_MEMORY_SOURCE_INVALID",
                "candidate does not match the successful run's approved memory binding",
                409,
            )


def _akb_memory_client(config: dict[str, Any] | None) -> AkbMemoryClient:
    if not isinstance(config, dict) or config.get("transport") not in {"http", "streamable_http"}:
        raise DigitalWorkerError(
            "DIGITAL_WORKER_MEMORY_CONNECTOR_UNAVAILABLE",
            "candidate's AKB connector is unavailable",
            409,
        )
    url = str(config.get("url") or "")
    try:
        validate_https_url(url, field="AKB connector URL")
    except ValueError:
        raise DigitalWorkerError(
            "DIGITAL_WORKER_MEMORY_CONNECTOR_INVALID",
            "candidate's AKB connector HTTPS endpoint is invalid",
            409,
        ) from None
    parsed = urlsplit(url)
    base_url = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    headers = config.get("headers")
    if not isinstance(headers, dict):
        headers = {}
    api_key = ""
    for key, value in headers.items():
        header = str(key).strip().lower()
        value = str(value).strip()
        if header == "x-api-key":
            api_key = value
            break
        if header == "authorization" and value.lower().startswith("bearer "):
            api_key = value[7:].strip()
    try:
        return AkbMemoryClient(AkbMemoryConfig(base_url=base_url, api_key=api_key))
    except ValueError:
        raise DigitalWorkerError(
            "DIGITAL_WORKER_MEMORY_CONNECTOR_INVALID",
            "candidate's AKB connector has no valid API key or HTTPS endpoint",
            409,
        ) from None
