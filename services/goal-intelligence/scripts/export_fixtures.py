"""Regenerate handoff fixtures from the real compiler + fixture extractor.

    python services/goal-intelligence/scripts/export_fixtures.py

Writes services/goal-intelligence/fixtures/*.json (deterministic IDs so diffs stay readable).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SERVICE_DIR), str(SERVICE_DIR / "tests")]

from conftest import USER_A, converse  # noqa: E402
from test_gi_compiler import CAFE_FLOW, PRODUCT_FLOW  # noqa: E402

from goal_intelligence.llm.base import ResilientExtractor  # noqa: E402
from goal_intelligence.llm.fixture import FixtureExtractor  # noqa: E402
from goal_intelligence.persistence.repository import sqlite_repository  # noqa: E402
from goal_intelligence.service import GoalIntelligenceService  # noqa: E402

SPEC_KEYS = ["goal_id", "owner_user_id", "version", "title", "goal_type", "criteria", "monitoring", "actions", "approved_at"]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        svc = GoalIntelligenceService(sqlite_repository(Path(tmp) / "gi.sqlite3"), ResilientExtractor(FixtureExtractor()))

        def draft(d, n):
            out = d.model_dump()
            out["session_id"] = f"ae000000-0000-4000-8000-0000000000d{n}"
            return out

        product = converse(svc, USER_A, PRODUCT_FLOW)
        cafe = converse(svc, USER_A, CAFE_FLOW)
        files = {
            "goal-draft.product.ready.json": draft(product, 1),
            "goal-draft.procurement.ready.json": draft(cafe, 2),
            "goal-draft.product.collecting.json": draft(svc.create_session(USER_A, PRODUCT_FLOW[0]), 3),
            "goal-draft.unsupported.json": draft(svc.create_session(USER_A, "Book me a flight to Rome under 200 EUR"), 4),
            "error.409.stale-revision.json": {
                "error": "conflict", "message": "stale revision; reload the draft", "current_revision": 2,
            },
        }
        for name, src, n in (("product", product, 1), ("procurement", cafe, 2)):
            spec = dict(src.candidate_goal)
            spec.update(
                goal_id=f"ae000000-0000-4000-8000-0000000000e{n}", owner_user_id=USER_A, version=1,
                approved_at="2026-11-14T12:00:00Z",
            )
            files[f"goal-spec.{name}.from-gi.json"] = {k: spec[k] for k in SPEC_KEYS}

    out_dir = SERVICE_DIR / "fixtures"
    out_dir.mkdir(exist_ok=True)
    for name, body in files.items():
        (out_dir / name).write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", name)


if __name__ == "__main__":
    main()
