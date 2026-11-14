from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

if sys.version_info < (3, 12):
    pytest.exit(
        f"goal-intelligence needs Python 3.12+ (AGENTS.md section 4); this is {sys.version.split()[0]}. "
        "Create a venv with python3.12 and install services/goal-intelligence/requirements.txt.",
        returncode=4,
    )

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from goal_intelligence.llm.base import ResilientExtractor  # noqa: E402
from goal_intelligence.llm.fixture import FixtureExtractor  # noqa: E402
from goal_intelligence.persistence.repository import sqlite_repository  # noqa: E402
from goal_intelligence.service import GoalIntelligenceService  # noqa: E402

USER_A = "ae000000-0000-4000-8000-00000000000a"
USER_B = "ae000000-0000-4000-8000-00000000000b"


def load_schema(name: str) -> dict:
    return json.loads((REPO_ROOT / "contracts" / "schemas" / name).read_text(encoding="utf-8"))


@pytest.fixture
def repo(tmp_path):
    return sqlite_repository(tmp_path / "gi.sqlite3")


@pytest.fixture
def make_service(repo):
    def _make(extractor=None, **limits):
        from goal_intelligence.service import Limits

        inner = extractor or FixtureExtractor()
        return GoalIntelligenceService(repo, ResilientExtractor(inner, sleep=lambda _s: None), Limits(**limits))

    return _make


@pytest.fixture
def service(make_service):
    return make_service()


@pytest.fixture
def goal_spec_validator():
    from jsonschema import Draft202012Validator, FormatChecker

    return Draft202012Validator(load_schema("goal-spec.v1.schema.json"), format_checker=FormatChecker())


@pytest.fixture
def goal_draft_validator():
    from jsonschema import Draft202012Validator, FormatChecker

    return Draft202012Validator(load_schema("goal-draft.v1.schema.json"), format_checker=FormatChecker())


def assemble_goal_spec(candidate: dict, owner: str = USER_A) -> dict:
    """What GM does on confirm: add server-managed fields, nothing else."""
    spec = dict(candidate)
    spec.update(
        goal_id=str(uuid.uuid4()), owner_user_id=owner, version=1, approved_at="2026-11-14T12:00:00Z"
    )
    return spec


def converse(service, owner: str, messages: list[str]):
    draft = service.create_session(owner, messages[0])
    for text in messages[1:]:
        draft = service.post_message(owner, draft.session_id, text, draft.revision)
    return draft
