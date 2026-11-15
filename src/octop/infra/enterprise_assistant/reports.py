"""Readable fact-only report drafts; no model guesses or hidden external calls."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from octop.i18n import tr


def health_status(value: Any) -> str:
    """Read old successful endpoint statuses without interpreting arbitrary text as healthy."""
    if isinstance(value, str) and value.lower() in {"healthy", "ok", "up", "pass"}:
        return "healthy"
    return value if value in ("abnormal", "unknown", "unavailable") else "unknown"


def format_report(sources: list[dict[str, Any]], locale: str) -> str:
    lines = [tr("enterprise_assistant.report_title", locale), ""]
    inspections = [s for s in sources if s.get("kind") == "inspection"]
    if inspections:
        counts = {"healthy": 0, "abnormal": 0, "unknown": 0, "unavailable": 0}
        for source in inspections:
            statuses = {
                health_status(f.get("status")) for f in source["facts"] if f.get("type") == "health"
            }
            status = (
                "unavailable"
                if source.get("collection_status") != "succeeded"
                else "abnormal"
                if "abnormal" in statuses
                else "healthy"
                if statuses == {"healthy"}
                else "unknown"
            )
            counts[status] += 1
        lines.extend(
            [
                tr(
                    "enterprise_assistant.inspection_coverage",
                    locale,
                    total=len(inspections),
                    collected=len(inspections) - counts["unavailable"],
                    **counts,
                ),
                "",
            ]
        )
    for source in sources:
        timestamp = datetime.fromtimestamp(source["collected_at"], UTC).isoformat()
        lines.extend(
            [
                str(source["name"]),
                tr(
                    "enterprise_assistant.inspection_source"
                    if source.get("kind") == "inspection"
                    else "enterprise_assistant.source",
                    locale,
                    run_id=source["run_id"],
                    at=timestamp,
                ),
            ]
        )
        facts = [f for f in source["facts"] if f.get("type") != "stage"]
        if not facts:
            lines.append(tr("enterprise_assistant.no_items", locale))
        for fact in facts:
            if fact.get("type") == "health":
                status = health_status(fact.get("status"))
                lines.append("- " + tr("enterprise_assistant.health_" + status, locale))
                metrics = fact.get("metrics", {})
                for key in ("cpu_percent", "memory_percent", "disk_percent"):
                    if key in metrics:
                        lines.append(f"  {key}: {metrics[key]}%")
            elif fact.get("type") == "ssh_fact":
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
