"""Persistence for enterprise scene definitions and approval-backed run snapshots."""

from __future__ import annotations

import json
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import now_ts


def _decode(row: Any) -> dict[str, Any]:
    item = dict(row)
    for key in ("mcp_servers_json",):
        raw = item.pop(key, "[]")
        try:
            value = json.loads(raw or "[]")
        except (TypeError, ValueError):
            value = []
        item["mcp_servers"] = [str(name) for name in value] if isinstance(value, list) else []
    return item


class EnterpriseSceneRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        *,
        scene_id: str,
        organization_id: str,
        position_id: str,
        agent_id: str,
        owner_user_id: int,
        name: str,
        prompt: str,
        mcp_servers: list[str],
    ) -> dict[str, Any]:
        now = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO enterprise_scenes(scene_id, organization_id, position_id, agent_id, "
                "owner_user_id, name, prompt, mcp_servers_json, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    scene_id,
                    organization_id,
                    position_id,
                    agent_id,
                    owner_user_id,
                    name,
                    prompt,
                    json.dumps(mcp_servers, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO enterprise_scene_versions(scene_id, version, name, prompt, "
                "mcp_servers_json, enabled, created_by, created_at) "
                "VALUES (?, 1, ?, ?, ?, 1, ?, ?)",
                (
                    scene_id,
                    name,
                    prompt,
                    json.dumps(mcp_servers, ensure_ascii=False),
                    owner_user_id,
                    now,
                ),
            )
        return self.get(scene_id)  # type: ignore[return-value]

    def get(self, scene_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM enterprise_scenes WHERE scene_id = ?", (scene_id,)
            ).fetchone()
        return _decode(row) if row else None

    def list_for_org(self, organization_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM enterprise_scenes WHERE organization_id = ? "
                "ORDER BY updated_at DESC, scene_id",
                (organization_id,),
            ).fetchall()
        return [_decode(row) for row in rows]

    def list_versions(self, scene_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT scene_id, version, name, prompt, mcp_servers_json, enabled, created_by, created_at "
                "FROM enterprise_scene_versions WHERE scene_id = ? ORDER BY version DESC",
                (scene_id,),
            ).fetchall()
        return [_decode(row) for row in rows]

    def update(
        self, scene_id: str, updates: dict[str, Any], *, expected_version: int, actor_id: int
    ) -> dict[str, Any] | None:
        allowed = {"name", "prompt", "mcp_servers", "enabled"}
        fields: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            column = "mcp_servers_json" if key == "mcp_servers" else key
            fields.append(f"{column} = ?")
            values.append(json.dumps(value, ensure_ascii=False) if key == "mcp_servers" else value)
        if not fields:
            return self.get(scene_id)
        now = now_ts()
        fields.extend(("version = version + 1", "updated_at = ?"))
        values.extend((now, scene_id, expected_version))
        with self._db.transaction() as conn:
            cur = conn.execute(
                f"UPDATE enterprise_scenes SET {', '.join(fields)} "
                "WHERE scene_id = ? AND version = ?",
                values,
            )
            if int(cur.rowcount or 0) != 1:
                return None
            row = conn.execute(
                "SELECT scene_id, version, name, prompt, mcp_servers_json, enabled "
                "FROM enterprise_scenes WHERE scene_id = ?",
                (scene_id,),
            ).fetchone()
            item = _decode(row)
            conn.execute(
                "INSERT INTO enterprise_scene_versions(scene_id, version, name, prompt, "
                "mcp_servers_json, enabled, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scene_id,
                    item["version"],
                    item["name"],
                    item["prompt"],
                    json.dumps(item["mcp_servers"], ensure_ascii=False),
                    item["enabled"],
                    actor_id,
                    now,
                ),
            )
        return self.get(scene_id)

    def create_run(
        self, *, run_id: str, scene: dict[str, Any], requested_by: int
    ) -> dict[str, Any]:
        now = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO enterprise_scene_runs(run_id, scene_id, organization_id, position_id, "
                "agent_id, scene_name, scene_version, prompt_snapshot, mcp_servers_json, requested_by, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_approval', ?, ?)",
                (
                    run_id,
                    scene["scene_id"],
                    scene["organization_id"],
                    scene["position_id"],
                    scene["agent_id"],
                    scene["name"],
                    scene["version"],
                    scene["prompt"],
                    json.dumps(scene["mcp_servers"], ensure_ascii=False),
                    requested_by,
                    now,
                    now,
                ),
            )
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM enterprise_scene_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _decode(row) if row else None

    def list_runs(
        self, organization_id: str, *, scene_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM enterprise_scene_runs WHERE organization_id = ?"
        params: list[Any] = [organization_id]
        if scene_id is not None:
            sql += " AND scene_id = ?"
            params.append(scene_id)
        sql += " ORDER BY created_at DESC, run_id"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_decode(row) for row in rows]

    def decide_run(
        self,
        run_id: str,
        *,
        approved_by: int,
        cron_id: str | None,
        status: str,
        note: str = "",
    ) -> bool:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE enterprise_scene_runs SET status = ?, approved_by = ?, cron_id = ?, "
                "decision_note = ?, updated_at = ? WHERE run_id = ? AND status = 'awaiting_approval'",
                (status, approved_by, cron_id, note, now_ts(), run_id),
            )
        return int(cur.rowcount or 0) == 1

    def mark_failed(self, run_id: str, *, summary: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE enterprise_scene_runs SET status = 'failed', error_summary = ?, "
                "updated_at = ? WHERE run_id = ? AND status IN ('awaiting_approval','queued')",
                (summary, now_ts(), run_id),
            )

    def sync_cron_status(self, run_id: str, *, status: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE enterprise_scene_runs SET status = ?, error_summary = ?, updated_at = ? "
                "WHERE run_id = ? AND status = 'queued'",
                (status, "Cron execution failed" if status == "failed" else None, now_ts(), run_id),
            )
