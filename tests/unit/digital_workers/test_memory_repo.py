from __future__ import annotations

from pathlib import Path

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.digital_workers import DigitalWorkerRepo


def _seed_candidate_parents(db: SqlitePool) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,1)",
            ("memory-reviewer", "hash", "user"),
        )
        conn.execute(
            "INSERT INTO agents(agent_id,name,created_at,updated_at) VALUES(?,?,1,1)",
            ("agent1", "Agent"),
        )
        conn.execute(
            "INSERT INTO organizations(organization_id,owner_user_id,name,created_at,updated_at) "
            "VALUES('org1',1,'Org',1,1)"
        )
        conn.execute(
            "INSERT INTO organization_positions(position_id,organization_id,title,created_at,updated_at) "
            "VALUES('position1','org1','Analyst',1,1)"
        )
        conn.execute(
            "INSERT INTO enterprise_scenes(scene_id,organization_id,position_id,agent_id,owner_user_id,"
            "name,prompt,created_at,updated_at) VALUES('scene1','org1','position1','agent1',1,"
            "'Scene','Prompt',1,1)"
        )
        for run_id in (
            "run-pending-candidate",
            "run-failed-candidate",
            "run-writing-candidate",
        ):
            conn.execute(
                "INSERT INTO enterprise_scene_runs(run_id,scene_id,organization_id,position_id,agent_id,"
                "scene_name,prompt_snapshot,requested_by,status,created_at,updated_at) "
                "VALUES(?,'scene1','org1','position1','agent1','Scene','Prompt',1,'succeeded',1,1)",
                (run_id,),
            )
        conn.execute(
            "INSERT INTO digital_workers(worker_id,organization_id,position_id,agent_id,owner_user_id,"
            "name,created_at,updated_at) VALUES('worker1','org1','position1','agent1',1,'Worker',1,1)"
        )


def _create_candidate(repo: DigitalWorkerRepo, candidate_id: str) -> None:
    repo.create_memory_candidate(
        candidate_id=candidate_id,
        organization_id="org1",
        worker_id="worker1",
        source_run_id=f"run-{candidate_id}",
        provider="akb",
        mcp_server="akb__1",
        scope_type="TASK",
        scope_code="weekly-report",
        content="Reviewed result",
        created_by=1,
    )


def test_memory_candidate_rejection_closes_pending_and_failed_states(
    tmp_path: Path,
) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    _seed_candidate_parents(db)
    repo = DigitalWorkerRepo(db)
    _create_candidate(repo, "pending-candidate")
    _create_candidate(repo, "failed-candidate")
    _create_candidate(repo, "writing-candidate")

    assert repo.claim_memory_candidate(
        "failed-candidate", reviewer_id=1, review_note="Retry once", content="Reviewed result"
    )
    assert repo.finish_memory_candidate("failed-candidate", status="failed")
    assert repo.claim_memory_candidate(
        "writing-candidate", reviewer_id=1, review_note="In progress", content="Reviewed result"
    )

    assert repo.reject_memory_candidate(
        "pending-candidate", reviewer_id=1, review_note="Not suitable"
    )
    assert repo.reject_memory_candidate(
        "failed-candidate", reviewer_id=1, review_note="Do not retry"
    )
    assert not repo.reject_memory_candidate(
        "writing-candidate", reviewer_id=1, review_note="Concurrent write"
    )

    assert repo.get_memory_candidate("pending-candidate")["status"] == "rejected"
    assert repo.get_memory_candidate("failed-candidate")["status"] == "rejected"
    assert repo.get_memory_candidate("writing-candidate")["status"] == "writing"
