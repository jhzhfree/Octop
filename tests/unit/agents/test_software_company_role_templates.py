"""Regression checks for bundled software-company role templates."""

from __future__ import annotations

import json
from pathlib import Path

ROLE_ROOT = Path(__file__).resolve().parents[3] / "src/octop/infra/agents/experts/library"
ROLE_IDS = {
    "software-general-manager",
    "software-product-manager",
    "software-project-manager",
    "software-architect",
    "software-backend-engineer",
    "software-frontend-engineer",
    "software-qa-engineer",
    "software-release-manager",
    "software-security-engineer",
    "software-data-analyst",
    "software-ux-designer",
    "software-customer-success",
    "software-sales-engineer",
    "software-marketing-manager",
    "software-hr-partner",
    "software-finance-analyst",
    "software-legal-compliance",
    "software-technical-writer",
}


def test_software_company_role_templates_are_complete_and_discoverable() -> None:
    from octop.infra.agents.experts.catalog import ExpertCatalog

    catalog = ExpertCatalog(ROLE_ROOT)
    catalog.refresh()
    assert ROLE_IDS.issubset({expert.id for expert in catalog.list_summaries()})

    for role_id in ROLE_IDS:
        role_dir = ROLE_ROOT / role_id
        manifest = json.loads((role_dir / "manifest.json").read_text(encoding="utf-8"))

        assert manifest["id"] == role_id
        assert manifest["label"]["zh"]
        assert manifest["label"]["en"]
        assert manifest["description"]["zh"]
        assert manifest["description"]["en"]
        assert "SOUL.md" in manifest["prompt_files"]

        soul = (role_dir / "SOUL.md").read_text(encoding="utf-8")
        assert "职责" in soul
        assert "权限与安全" in soul
        assert len(soul) > 500
