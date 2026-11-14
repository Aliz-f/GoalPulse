"""Controlled live smoke test for the Anthropic provider (R2-D2-01).

Opt-in only, and chargeable: makes at most TWO model calls (one per scenario, retries off)
against a throwaway SQLite database. Prompts are synthetic and non-sensitive. Prints
latency, error codes, the generated question and missing fields; never the API key.

    GI_LIVE_SMOKE=1 GOALPULSE_PROFILE=demo GI_LLM_PROVIDER=anthropic \
    GI_LLM_MODEL=<model your account can use> ANTHROPIC_API_KEY=... \
    python services/goal-intelligence/scripts/live_smoke.py

Exit 0 = PASS (both drafts valid, dynamic, different questions, nothing confirmable).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]
sys.path.insert(0, str(SERVICE_DIR))

from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402

from goal_intelligence.config import ConfigError, Settings, build_service, runtime_disclosure  # noqa: E402
from goal_intelligence.service import RETRY_NOTICE  # noqa: E402

SCENARIOS = [
    ("product", "I want to buy a Kindle Paperwhite e-reader for under 180 euros"),
    ("supplies", "Our small café needs 10 kg of coffee beans every month and we spend about 200 euros on it now"),
]


def main() -> int:
    if os.environ.get("GI_LIVE_SMOKE") != "1":
        print("Refusing to run: set GI_LIVE_SMOKE=1 to allow up to 2 chargeable Anthropic calls.")
        return 2
    try:
        with tempfile.TemporaryDirectory() as tmp:
            settings = replace(Settings(), database_url=f"sqlite:///{Path(tmp) / 'smoke.db'}", llm_max_attempts=1)
            if settings.llm_provider != "anthropic":
                print("Refusing to run: GI_LLM_PROVIDER must be 'anthropic' for the live smoke test.")
                return 2
            service = build_service(settings)
            print("runtime:", json.dumps(runtime_disclosure(settings)))
            schema = json.loads((REPO_ROOT / "contracts/schemas/goal-draft.v1.schema.json").read_text())
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            owner = str(uuid.uuid4())
            questions: list[str] = []
            failures: list[str] = []
            for label, text in SCENARIOS:
                started = time.monotonic()
                draft = service.create_session(owner, text)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                codes = [a.error_code or "ok" for a in service.extractor.attempts]
                errors = sorted(e.message for e in validator.iter_errors(draft.model_dump()))
                print(f"\n[{label}] {elapsed_ms} ms, attempts={codes}")
                print(f"  goal_type={draft.candidate_goal.get('goal_type')!r} missing={draft.missing_fields}")
                print(f"  question: {draft.next_question}")
                if errors:
                    failures.append(f"{label}: GoalDraft schema errors {errors}")
                if draft.ready_for_confirmation:
                    failures.append(f"{label}: first turn must not be confirmable")
                if not draft.next_question or draft.next_question.startswith(RETRY_NOTICE):
                    failures.append(f"{label}: provider did not produce a usable draft (codes {codes})")
                questions.append(draft.next_question or "")
            if len(set(questions)) != len(questions):
                failures.append("different goals produced the same question (not dynamic)")
    except ConfigError as exc:
        print(f"CONFIG FAIL: {exc}")
        return 1
    print("\nRESULT:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  -", failure)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
