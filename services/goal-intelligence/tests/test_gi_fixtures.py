"""Handoff fixtures for D1 (GoalDraft per type, 409 body) and D4 (confirmed GoalSpec)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import SERVICE_DIR

FIXTURES = SERVICE_DIR / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("goal-draft.*.json")))
def test_draft_fixtures_match_goal_draft_v1(name, goal_draft_validator):
    goal_draft_validator.validate(_load(name))


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("goal-spec.*.json")))
def test_spec_fixtures_match_goal_spec_v1(name, goal_spec_validator):
    spec = _load(name)
    goal_spec_validator.validate(spec)
    assert spec["actions"]["auto_purchase"] is False


def test_ready_drafts_are_exactly_what_gm_assembles(goal_spec_validator):
    for kind in ("product", "procurement"):
        draft = _load(f"goal-draft.{kind}.ready.json")
        spec = _load(f"goal-spec.{kind}.from-gi.json")
        assert draft["ready_for_confirmation"] is True
        for key in ("title", "goal_type", "criteria", "monitoring", "actions"):
            assert spec[key] == draft["candidate_goal"][key]
