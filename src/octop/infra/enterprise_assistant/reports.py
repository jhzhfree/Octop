"""Readable fact-only report drafts; no model guesses or hidden external calls."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from octop.i18n import tr


def format_report(sources: list[dict[str, Any]], locale: str) -> str:
    lines = [tr("enterprise_assistant.report_title", locale), ""]
    for source in sources:
        timestamp = datetime.fromtimestamp(source["collected_at"], UTC).isoformat()
        lines.extend(
            [
                str(source["name"]),
                tr("enterprise_assistant.source", locale, run_id=source["run_id"], at=timestamp),
            ]
        )
        facts = [f for f in source["facts"] if f.get("type") != "stage"]
        if not facts:
            lines.append(tr("enterprise_assistant.no_items", locale))
        for fact in facts:
            if fact.get("type") == "ssh_fact":
                lines.append(f"- {fact.get('command', '')}:\n{fact.get('output', '')}")
            else:
                details = [
                    str(fact.get(k) or "")
                    for k in ("key", "name", "summary", "value", "status", "due", "url")
                ]
                lines.append("- " + " · ".join(v for v in details if v))
        lines.append("")
    lines.append(tr("enterprise_assistant.review_notes", locale))
    return "\n".join(lines)
