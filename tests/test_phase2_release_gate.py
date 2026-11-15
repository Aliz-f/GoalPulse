from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "infrastructure" / "release"


def manifest():
    return json.loads((RELEASE / "scenarios.json").read_text(encoding="utf-8"))


def runner_module():
    spec = importlib.util.spec_from_file_location("phase2_gate", RELEASE / "run_phase2_gate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_manifest_covers_every_a1_through_d5_scenario():
    data = manifest()
    expected = {f"A{number}" for number in range(1, 4)}
    expected |= {f"B{number}" for number in range(1, 6)}
    expected |= {f"C{number}" for number in range(1, 4)}
    expected |= {f"D{number}" for number in range(1, 6)}
    assert set(data["scenarios"]) == expected
    referenced = {
        scenario
        for entries in data["lanes"].values()
        for entry in entries
        for scenario in entry["scenarios"]
    }
    assert referenced == expected


def test_recorded_lane_cannot_receive_live_provider_credentials_or_hide_live_requirement():
    source = (RELEASE / "run_phase2_gate.py").read_text(encoding="utf-8")
    assert 'environment["ANTHROPIC_API_KEY"] = ""' in source
    assert 'environment["CONTEXT_DEV_API_KEY"] = ""' in source
    data = manifest()
    assert data["scenarios"]["A1"]["live_required"] is True
    assert data["scenarios"]["B1"]["live_required"] is True
    assert data["lanes"]["live"][0]["manual"] is True
    assert "live" not in runner_module().SAFE_LANES


def test_release_evidence_redacts_secrets_dsns_and_raw_goal_text():
    redacted = runner_module()._redact(
        'Authorization: Bearer abc.def.ghi\n'
        'postgresql://owner:secret@localhost:5432/db\n'
        'ANTHROPIC_API_KEY=provider-secret\n'
        '"initial_message": "private goal"\n'
        '"provider_response": "raw body"'
    )
    for secret in ("abc.def.ghi", "owner:secret", "provider-secret", "private goal", "raw body"):
        assert secret not in redacted
    assert redacted.count("[REDACTED]") >= 5


def test_release_runner_lists_without_executing_or_requiring_credentials():
    result = subprocess.run(
        [sys.executable, str(RELEASE / "run_phase2_gate.py"), "--list"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    body = json.loads(result.stdout)
    assert body["lanes"] == ["recorded", "durable", "frontend", "live"]
    assert body["scenarios"][0] == "A1" and body["scenarios"][-1] == "D5"


def test_release_commands_do_not_add_purchase_payment_or_booking_actions():
    serialized = json.dumps(manifest()).lower()
    for forbidden in ("checkout/", "payment/", "booking/", "auto_purchase=true", "auto_execute=true"):
        assert forbidden not in serialized


def test_release_runner_has_a_bounded_suite_timeout():
    source = (RELEASE / "run_phase2_gate.py").read_text(encoding="utf-8")
    assert 'timeout=entry.get("timeout_seconds", 600)' in source
    assert "except subprocess.TimeoutExpired" in source


def test_compose_ci_environment_uses_nondefault_loopback_ports():
    source = (RELEASE / "write_compose_ci_env.py").read_text(encoding="utf-8")
    for name in ("POSTGRES_HOST_PORT", "RABBITMQ_HOST_PORT", "RABBITMQ_MANAGEMENT_HOST_PORT",
                 "GATEWAY_HOST_PORT", "FRONTEND_HOST_PORT"):
        assert f'"{name}"' in source
    assert "secrets.token_urlsafe" in source
    assert "ci_local_postgres_password" not in source


def test_frontend_lane_proves_demo_mock_and_missing_mode_are_refused():
    entries = {entry["id"]: entry for entry in manifest()["lanes"]["frontend"]}
    for suite in ("frontend_refuses_demo_mock", "frontend_refuses_missing_mode"):
        assert entries[suite]["expected_exit_code"] == 1
        assert entries[suite]["expected_output"]


def test_release_workflow_requires_recovery_after_database_and_broker_restart():
    workflow = (ROOT / ".github/workflows/phase2-release.yml").read_text(encoding="utf-8")
    assert "restart postgres rabbitmq" in workflow
    assert workflow.count("--wait --wait-timeout 240") == 2
