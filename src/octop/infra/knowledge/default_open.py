"""Knowledge-base selection defaults for a single chat turn."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from octop.infra.db.repos.knowledge import KnowledgeBaseRow
from octop.infra.knowledge.akb_identity import delegated_identity_enabled


def merge_knowledge_base_ids(
    visible_bases: Sequence[KnowledgeBaseRow],
    explicit_ids: list[str] | None,
    *,
    owner_user_id: int,
    extra_ids: Sequence[str] | None = None,
) -> list[str]:
    """Use the actor's own default-open bases when a turn omits a list.

    ``default_open`` is per-owner preference: shared bases marked default-open
    are auto-injected only for the creating user, not for other viewers.
    ``extra_ids`` (expert composer picks) are unioned in when still visible.
    """
    if explicit_ids is not None:
        return list(explicit_ids)
    selected: list[str] = []
    visible_ids = {base.id for base in visible_bases}
    for base in visible_bases:
        if base.default_open and int(base.owner_user_id) == int(owner_user_id):
            selected.append(base.id)
    for kb_id in extra_ids or []:
        text = str(kb_id).strip()
        if text and text in visible_ids and text not in selected:
            selected.append(text)
    return selected


def stamp_turn_knowledge_config(
    request: dict[str, Any],
    *,
    visible_bases: Sequence[KnowledgeBaseRow],
    explicit_ids: list[str] | None,
    owner_user_id: int,
    extra_ids: Sequence[str] | None = None,
    is_admin: bool = False,
    locale: str,
) -> list[str]:
    """Write this turn's knowledge-base selection onto the harness request."""
    from octop.infra.knowledge.hint import catalog_for_selected_bases  # noqa: PLC0415

    selected_ids = merge_knowledge_base_ids(
        visible_bases,
        explicit_ids,
        owner_user_id=owner_user_id,
        extra_ids=extra_ids,
    )
    configurable = dict(request.get("configurable") or {})
    configurable["knowledge_base_ids"] = selected_ids
    configurable["knowledge_base_catalog"] = catalog_for_selected_bases(visible_bases, selected_ids)
    configurable["user_is_admin"] = is_admin
    configurable["locale"] = locale
    # Do not preserve identity controls from caller-supplied configurable data.
    configurable.pop("octop_akb_identity_mode", None)
    configurable.pop("octop_trace_id", None)
    if delegated_identity_enabled():
        # These values come from the server-built request, never from model or
        # client supplied tool arguments.
        configurable.pop("agent_id", None)
        if request.get("agent_id"):
            configurable["agent_id"] = str(request["agent_id"])
        if request.get("thread_id"):
            configurable["octop_trace_id"] = str(request["thread_id"])
        configurable["octop_akb_identity_mode"] = (
            "AGENT_SERVICE"
            if str(request.get("source") or "").lower() == "cron"
            else "AGENT_DELEGATED_USER"
        )
    request["configurable"] = configurable
    return selected_ids
