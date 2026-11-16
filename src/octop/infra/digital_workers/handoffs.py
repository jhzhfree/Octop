"""Explicit organizational handoff; creates a draft, never executes or grants access."""

from __future__ import annotations

from typing import Any

from octop.infra.db.repos.goal_handoffs import GoalHandoffRepo, HandoffError
from octop.infra.digital_workers.service import DigitalWorkerError, DigitalWorkerService


class GoalHandoffService(DigitalWorkerService):
    @staticmethod
    def error(exc: HandoffError) -> DigitalWorkerError:
        messages = {
            "forbidden": (403, "Current manager and target Agent access required"),
            "not_found": (404, "Source goal or receiving worker not found in this organization"),
            "conflict": (
                409,
                "Handoff conflicts with current versions, roles or the original request; reload before a new request",
            ),
            "sqlite_required": (503, "Goal handoff creation currently requires SQLite"),
        }
        status, message = messages[exc.reason]
        return DigitalWorkerError("GOAL_HANDOFF_" + exc.reason.upper(), message, status)

    def create_handoff(
        self, org: str, worker: str, goal: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_manager(org)
        for field, maximum in (("title", 200), ("reason", 2000), ("success_criteria", 4000)):
            value = self._required_text(request[field], field)
            if len(value) > maximum:
                raise DigitalWorkerError("GOAL_HANDOFF_INVALID", "handoff field too long", 422)
            request[field] = value
        try:
            return GoalHandoffRepo(self.repos.db).create(org, worker, goal, self.actor.id, request)
        except HandoffError as exc:
            raise self.error(exc) from exc

    def handoffs(self, org: str, goal: str | None, before: str | None) -> dict[str, Any]:
        self._require_manager(org)
        try:
            return GoalHandoffRepo(self.repos.db).list(org, self.actor.id, goal, before)
        except HandoffError as exc:
            raise self.error(exc) from exc
