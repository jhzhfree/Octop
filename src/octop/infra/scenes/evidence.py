"""Read the visible assistant result for an approved enterprise scene run."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import messages_from_dict

from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.history.legacy import checkpoint_wires
from octop.infra.history.service import HistoryArchive
from octop.infra.utils.llm_text import strip_thinking

_MAX_EVIDENCE_CHARS = 20_000


def latest_assistant_answer(wires: list[dict[str, Any]]) -> str | None:
    """Return visible assistant text only; omit reasoning and tool payloads."""
    answer = ""
    for message in messages_from_dict(wires):
        if getattr(message, "type", None) != "ai":
            continue
        content = message.content
        if isinstance(content, str):
            text = strip_thinking(content)
        elif isinstance(content, list):
            text = strip_thinking(
                "\n".join(
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            )
        else:
            text = ""
        if text:
            answer = text
    return answer[:_MAX_EVIDENCE_CHARS] or None


async def scene_run_evidence(
    *,
    run: dict[str, Any],
    cron_manager: Any,
    thread_registry: Any,
    agent_registry: Any,
    thread_message_repo: Any,
    history_archive: Any,
) -> str | None:
    """Resolve a run's isolated transcript and return its last visible answer."""
    cron_id = run.get("cron_id")
    if not cron_id:
        return None
    cron = cron_manager.get(str(cron_id))
    if cron is None:
        return None
    expected_key = ThreadRegistry.make_key(
        agent_id=str(run["agent_id"]),
        channel_type="cron",
        channel_subject_id=str(cron_id),
        channel_chat_type=ThreadRegistry.CHAT_TYPE_DM,
    )
    if cron.session_key != expected_key:
        return None
    session = thread_registry.get_session(expected_key)
    if (
        session is None
        or session.agent_id != run["agent_id"]
        or session.user_id != run["requested_by"]
    ):
        return None

    if isinstance(history_archive, HistoryArchive):
        from octop.infra.history.reader import read_page  # noqa: PLC0415

        page = await read_page(
            history_archive,
            agent_registry,
            str(run["agent_id"]),
            session.thread_id,
            limit=200,
        )
        wires = page["messages"]
    elif thread_message_repo.projection_status(session.thread_id) == "ready":
        rows, _has_more = thread_message_repo.page(session.thread_id, limit=200)
        wires = [json.loads(row.message_json) for row in rows]
    else:
        harness = agent_registry.get_agent(str(run["agent_id"]))
        wires = await checkpoint_wires(harness, session.thread_id)
    return latest_assistant_answer(wires)
