"""Bounded persisted-run aggregation, without evidence or state reconciliation."""

from typing import Any

from octop.infra.db.pool import DatabasePool


class ProgressAccessError(Exception):
    """The current user may not read this organization's summary."""


class OrganizationProgressRepo:
    MAX_RUNS = 10000

    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    @staticmethod
    def _authorize(conn: Any, organization_id: str, user_id: int) -> None:
        row = conn.execute(
            "SELECT u.role,u.disabled,m.role AS member_role FROM users u "
            "LEFT JOIN organization_members m ON m.user_id=u.id AND m.organization_id=? "
            "WHERE u.id=?",
            (organization_id, user_id),
        ).fetchone()
        if (
            row is None
            or row["disabled"]
            or not (row["role"] == "admin" or row["member_role"] in {"owner", "manager"})
        ):
            raise ProgressAccessError("organization manager access required")

    def summarize(self, organization_id: str, user_id: int, start: int, end: int) -> dict[str, Any]:
        with self.db.connect() as conn:
            self._authorize(conn, organization_id, user_id)
            rows = conn.execute(
                "WITH recent AS (SELECT run_id,organization_id,status "
                "FROM enterprise_scene_runs WHERE organization_id=? "
                "AND created_at>=? AND created_at<? LIMIT ?) "
                "SELECT r.status,a.decision,COUNT(*) AS n FROM recent r "
                "LEFT JOIN scene_run_acceptances a ON a.run_id=r.run_id "
                "AND a.organization_id=r.organization_id GROUP BY r.status,a.decision",
                (organization_id, start, end, self.MAX_RUNS + 1),
            ).fetchall()
            # A second authorization check catches revocation during the read. This is
            # not a transaction-wide authorization guarantee or execution permission.
            self._authorize(conn, organization_id, user_id)
        observed = sum(int(row["n"]) for row in rows)
        if observed > self.MAX_RUNS:
            # Never show arbitrary partial counts as complete totals.
            return {"limit_exceeded": True, "counts": None, "denominator": None}
        execution = dict.fromkeys(
            ("awaiting_approval", "rejected", "queued", "succeeded", "failed"), 0
        )
        delivery = dict.fromkeys(("unreviewed_success", "accepted", "changes_requested"), 0)
        inconsistent = 0
        for row in rows:
            status, decision, n = row["status"], row["decision"], int(row["n"])
            invalid = False
            if status in execution:
                execution[status] += n
            else:
                invalid = True
            if status == "succeeded":
                if decision is None:
                    delivery["unreviewed_success"] += n
                elif decision in delivery:
                    delivery[decision] += n
                else:
                    invalid = True
            elif decision is not None:
                invalid = True
            if invalid:
                inconsistent += n
        return {
            "limit_exceeded": False,
            "denominator": observed,
            "counts": {"execution": execution, "delivery": delivery},
            "inconsistent_records": inconsistent,
        }

    def queue(
        self,
        organization_id: str,
        user_id: int,
        *,
        category: str,
        limit: int,
        before: int | None,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        # Predicates are selected by the service, never interpolated from a request.
        predicates = {
            "awaiting_approval": "r.status='awaiting_approval' AND a.decision IS NULL",
            "queued": "r.status='queued' AND a.decision IS NULL",
            "rejected": "r.status='rejected' AND a.decision IS NULL",
            "failed": "r.status='failed' AND a.decision IS NULL",
            "unreviewed_success": "r.status='succeeded' AND a.decision IS NULL",
            "changes_requested": "r.status='succeeded' AND a.decision='changes_requested'",
            "accepted": "r.status='succeeded' AND a.decision='accepted'",
        }
        predicate = predicates[category]
        worker_predicate = (
            " AND EXISTS (SELECT 1 FROM digital_worker_plans p "
            "JOIN digital_worker_goals g ON g.goal_id=p.goal_id "
            "JOIN digital_workers w ON w.worker_id=g.worker_id "
            "WHERE p.plan_id=r.plan_id AND w.worker_id=? AND w.organization_id=r.organization_id)"
            if worker_id is not None
            else ""
        )

        def authorize_worker(conn: Any) -> None:
            if (
                worker_id is not None
                and conn.execute(
                    "SELECT 1 FROM digital_workers WHERE worker_id=? AND organization_id=?",
                    (worker_id, organization_id),
                ).fetchone()
                is None
            ):
                raise ProgressAccessError("organization worker access required")

        with self.db.connect() as conn:
            self._authorize(conn, organization_id, user_id)
            authorize_worker(conn)
            rows = conn.execute(
                "SELECT r.id,r.run_id,r.status,r.created_at,a.decision "
                "FROM enterprise_scene_runs r LEFT JOIN scene_run_acceptances a "
                "ON a.run_id=r.run_id AND a.organization_id=r.organization_id "
                "WHERE r.organization_id=? AND "
                + predicate
                + worker_predicate
                + (" AND r.id<?" if before is not None else "")
                + " ORDER BY r.id DESC LIMIT ?",
                (
                    organization_id,
                    *((worker_id,) if worker_id is not None else ()),
                    *((before,) if before is not None else ()),
                    limit + 1,
                ),
            ).fetchall()
            self._authorize(conn, organization_id, user_id)
            authorize_worker(conn)
        page = rows[:limit]
        return {
            "items": [
                {
                    "run_id": row["run_id"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "decision": row["decision"],
                }
                for row in page
            ],
            "next_before": int(page[-1]["id"]) if len(rows) > limit else None,
        }

    def detail(self, organization_id: str, user_id: int, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            self._authorize(conn, organization_id, user_id)
            row = conn.execute(
                "SELECT r.run_id,r.organization_id,r.scene_id,r.scene_name,r.scene_version,"
                "r.plan_id,r.status,r.created_at,r.updated_at,a.decision "
                "FROM enterprise_scene_runs r LEFT JOIN scene_run_acceptances a "
                "ON a.run_id=r.run_id AND a.organization_id=r.organization_id "
                "WHERE r.organization_id=? AND r.run_id=?",
                (organization_id, run_id),
            ).fetchone()
            self._authorize(conn, organization_id, user_id)
        return dict(row) if row is not None else None
