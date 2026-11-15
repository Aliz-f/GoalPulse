#!/usr/bin/env python3
"""Run one R2-D1-03 release lane and write redacted evidence.

The default lanes are deterministic and must not make provider calls. The live
entry is deliberately manual: this runner reports it NOT RUN instead of turning
missing credentials into a fixture success.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("scenarios.json")
SAFE_LANES = {"recorded", "durable", "frontend"}
SECRET_NAMES = (
    "ANTHROPIC_API_KEY",
    "CONTEXT_DEV_API_KEY",
    "GATEWAY_DEMO_SESSION_SECRET",
    "GATEWAY_DEV_JWT_SECRET",
    "SERVICE_JWT_SECRET",
    "POSTGRES_PASSWORD",
    "RABBITMQ_DEFAULT_PASS",
)


def _redact(text: str) -> str:
    result = text
    result = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]", result)
    result = re.sub(r"(?i)\b(postgresql|postgres|amqp|amqps)://[^\s/@:]+:[^\s/@]+@", r"\1://[REDACTED]@", result)
    result = re.sub(
        r'(?i)("?(?:password|initial_message|prompt|provider_response|raw_goal)"?\s*[:=]\s*)' r'("[^"]*"|\S+)',
        r"\1[REDACTED]",
        result,
    )
    for name in SECRET_NAMES:
        result = re.sub(rf"(?i)({re.escape(name)}\s*=\s*)\S+", r"\1[REDACTED]", result)
    return result


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _command(entry: dict[str, Any]) -> list[str]:
    return [sys.executable if value == "{python}" else value for value in entry["command"]]


def _diagnostic(output: str, *, limit: int = 30) -> list[str]:
    lines = [line for line in _redact(output).splitlines() if line.strip()]
    return lines[-limit:]


def run_lane(lane: str, output_path: Path) -> int:
    manifest = load_manifest()
    if lane not in manifest["lanes"]:
        raise SystemExit(f"unknown lane: {lane}")
    if lane == "live":
        results = [
            {
                "id": entry["id"],
                "status": "NOT_RUN",
                "reason": entry["reason"],
                "scenarios": entry["scenarios"],
            }
            for entry in manifest["lanes"][lane]
        ]
        exit_code = 2
    else:
        if lane not in SAFE_LANES:
            raise SystemExit(f"lane is not executable by this runner: {lane}")
        environment = os.environ.copy()
        if lane == "recorded":
            # Recorded tests must explicitly inject transports. Empty provider
            # credentials make accidental external calls fail closed.
            environment["ANTHROPIC_API_KEY"] = ""
            environment["CONTEXT_DEV_API_KEY"] = ""
        results = []
        exit_code = 0
        for entry in manifest["lanes"][lane]:
            command = _command(entry)
            cwd = ROOT / entry.get("cwd", ".")
            entry_environment = environment | entry.get("env", {})
            for name in entry.get("unset_env", []):
                entry_environment.pop(name, None)
            started = datetime.now(timezone.utc)
            try:
                completed = subprocess.run(
                    command,
                    cwd=cwd,
                    env=entry_environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=entry.get("timeout_seconds", 600),
                    check=False,
                )
                return_code = completed.returncode
                command_output = completed.stdout
            except subprocess.TimeoutExpired as error:
                return_code = 124
                partial = error.stdout or ""
                command_output = (partial.decode(errors="replace") if isinstance(partial, bytes) else partial)
                command_output += "\nrelease suite exceeded its bounded timeout"
            finished = datetime.now(timezone.utc)
            sanitized = _redact(command_output)
            expected_return_code = entry.get("expected_exit_code", 0)
            expected_output = entry.get("expected_output")
            passed = return_code == expected_return_code and (
                expected_output is None or expected_output in command_output
            )
            result = {
                "id": entry["id"],
                "status": "PASS" if passed else "FAIL",
                "exit_code": return_code,
                "expected_exit_code": expected_return_code,
                "duration_seconds": round((finished - started).total_seconds(), 3),
                "command": command,
                "cwd": str(cwd.relative_to(ROOT)) or ".",
                "scenarios": entry["scenarios"],
                "output_sha256": hashlib.sha256(sanitized.encode()).hexdigest(),
                "diagnostic_tail": _diagnostic(command_output),
            }
            results.append(result)
            print(f"{result['status']:4} {entry['id']} ({result['duration_seconds']}s)", flush=True)
            if not passed:
                exit_code = 1

    scenario_results: dict[str, str] = {}
    for scenario_id in manifest["scenarios"]:
        matches = [item for item in results if scenario_id in item["scenarios"]]
        if not matches:
            scenario_results[scenario_id] = "NOT_RUN"
        elif any(item["status"] == "FAIL" for item in matches):
            scenario_results[scenario_id] = "FAIL"
        elif all(item["status"] == "PASS" for item in matches):
            scenario_results[scenario_id] = "PASS"
        else:
            scenario_results[scenario_id] = "NOT_RUN"

    report = {
        "schema_version": 1,
        "task": manifest["task"],
        "lane": lane,
        "mode": "recorded_or_local" if lane != "live" else "controlled_live_manual",
        "live_deployment": lane == "live",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_commit": _git_commit(),
        "python": sys.version.split()[0],
        "overall": "PASS" if exit_code == 0 else ("NOT_RUN" if exit_code == 2 else "FAIL"),
        "scenario_results": scenario_results,
        "results": results,
        "redaction": "Secrets, authorization values, DSNs, prompts, raw goals, and provider responses are excluded or redacted.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane", choices=("recorded", "durable", "frontend", "live"))
    parser.add_argument("--output", type=Path, default=Path("/tmp/goalpulse-r2-d1-03.json"))
    parser.add_argument("--list", action="store_true", help="List lanes and scenario IDs without running tests")
    args = parser.parse_args()
    manifest = load_manifest()
    if args.list:
        print(json.dumps({"lanes": list(manifest["lanes"]), "scenarios": list(manifest["scenarios"])}, sort_keys=True))
        return 0
    if not args.lane:
        parser.error("--lane is required unless --list is used")
    return run_lane(args.lane, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
