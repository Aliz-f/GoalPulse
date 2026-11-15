#!/usr/bin/env python3
"""Interactively create, repair, inspect, or validate GoalPulse's root .env."""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import getpass
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from typing import Callable
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from validate_env import DEFAULT_COMPOSE, DEFAULT_ENV, PLACEHOLDER_MARKERS, parse_env, validate


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE = ROOT / ".env.example"
NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
HOST_PATTERN = re.compile(
    r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
)
PASSWORD_HASH_PATTERN = re.compile(
    r"pbkdf2_sha256\$(\d+)\$[A-Za-z0-9_-]+\$[A-Za-z0-9_-]+"
)
SECRET_NAMES = {
    "POSTGRES_PASSWORD",
    "GOAL_INTELLIGENCE_DB_PASSWORD",
    "GOAL_MANAGEMENT_DB_PASSWORD",
    "DISCOVERY_MONITORING_DB_PASSWORD",
    "DATA_EVIDENCE_DB_PASSWORD",
    "GOAL_EVALUATION_DB_PASSWORD",
    "NOTIFICATION_DB_PASSWORD",
    "AGENT_INTEGRATION_DB_PASSWORD",
    "RABBITMQ_DEFAULT_PASS",
    "GATEWAY_DEV_JWT_SECRET",
    "SERVICE_JWT_SECRET",
    "GATEWAY_DEMO_SESSION_SECRET",
}
API_KEY_NAMES = {"ANTHROPIC_API_KEY", "CONTEXT_DEV_API_KEY"}
PASSWORD_HASH_NAMES = {
    "GATEWAY_DEMO_USER_A_PASSWORD_HASH",
    "GATEWAY_DEMO_USER_B_PASSWORD_HASH",
}
UUID_NAMES = {"GATEWAY_DEMO_USER_A_ID", "GATEWAY_DEMO_USER_B_ID"}
RANDOM_GENERATABLE_NAMES = SECRET_NAMES | UUID_NAMES
GENERATABLE_NAMES = RANDOM_GENERATABLE_NAMES | PASSWORD_HASH_NAMES
PORT_NAMES = {
    "FRONTEND_HOST_PORT",
    "GATEWAY_HOST_PORT",
    "POSTGRES_HOST_PORT",
    "RABBITMQ_HOST_PORT",
    "RABBITMQ_MANAGEMENT_HOST_PORT",
}
CHOICES = {
    "GOALPULSE_PROFILE": {"test", "dev", "demo"},
    "NEXT_PUBLIC_API_MODE": {"mock", "gateway"},
    "POSTGRES_DB": {"postgres"},
    "GI_LLM_PROVIDER": {"anthropic", "fixture"},
    "GI_LLM_EFFORT": {"", "low", "medium", "high"},
    "DM_PROVIDER": {"context_dev"},
    "DE_EVIDENCE_MODE": {"real", "fixture"},
    "DE_EVIDENCE_PROVIDER": {"real", "fixture"},
    "DE_ALLOW_FIXTURE": {"true", "false"},
}
INTEGER_RANGES = {
    "GATEWAY_DEMO_SESSION_TTL_SECONDS": (300, 3600),
    "GI_LLM_MAX_ATTEMPTS": (1, 5),
    "GI_MAX_MESSAGE_CHARS": (1, 100_000),
    "GI_MAX_USER_TURNS": (1, 10_000),
    "DM_MAX_ACTIVE_GOALS_PER_USER": (1, 2),
    "DM_MAX_SOURCES_PER_GOAL": (1, 3),
    "DM_INITIAL_MAX_OPERATIONS_PER_DAY": (1, 100),
    "DM_MAX_REQUESTS_PER_USER_PER_DAY": (1, 100),
    "NOTIFY_GOAL_RATE_LIMIT_MINUTES": (60, 1440),
}


@dataclass(frozen=True)
class Field:
    name: str
    default: str
    description: str
    options: str


@dataclass(frozen=True)
class PromptResult:
    value: str
    skipped: bool = False


def parse_template(path: Path) -> tuple[list[str], list[Field]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    fields: list[Field] = []
    description: str | None = None
    options: str | None = None
    for line_number, line in enumerate(lines, 1):
        if line.startswith("# Description: "):
            description = line.removeprefix("# Description: ").strip()
            continue
        if line.startswith("# Options: "):
            options = line.removeprefix("# Options: ").strip()
            continue
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if not NAME_PATTERN.fullmatch(name):
            raise ValueError(f"template line {line_number} has an invalid variable name")
        if description is None or options is None:
            raise ValueError(f"{name} must have adjacent Description and Options comments")
        fields.append(Field(name, value, description, options))
        description = options = None
    names = [field.name for field in fields]
    if len(names) != len(set(names)):
        raise ValueError("template contains duplicate variables")
    return lines, fields


def _has_placeholder(value: str) -> bool:
    return any(marker in value for marker in PLACEHOLDER_MARKERS)


def _required(name: str, values: dict[str, str]) -> bool:
    profile = values.get("GOALPULSE_PROFILE", "demo")
    if name == "NEXT_PUBLIC_GATEWAY_URL":
        return values.get("NEXT_PUBLIC_API_MODE") == "gateway"
    if name in {
        "GATEWAY_DEMO_SESSION_SECRET",
        "GATEWAY_DEMO_USER_A_ID",
        "GATEWAY_DEMO_USER_A_USERNAME",
        "GATEWAY_DEMO_USER_A_PASSWORD_HASH",
        "GATEWAY_DEMO_USER_A_DISPLAY_NAME",
        "GATEWAY_DEMO_USER_B_ID",
        "GATEWAY_DEMO_USER_B_USERNAME",
        "GATEWAY_DEMO_USER_B_PASSWORD_HASH",
        "GATEWAY_DEMO_USER_B_DISPLAY_NAME",
    }:
        return profile == "demo"
    if name == "CONTEXT_DEV_ACCESS_POLICY_IDS":
        return profile == "demo"
    if name in {"GI_LLM_MODEL", "ANTHROPIC_API_KEY"}:
        return values.get("GI_LLM_PROVIDER") == "anthropic"
    if name in {"GI_LLM_EFFORT", "DM_OPERATOR_IDS"}:
        return False
    return True


def _origin(value: str, *, https_only: bool) -> bool:
    try:
        parsed = urlsplit(value)
        _port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in ({"https"} if https_only else {"http", "https"})
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _integer_in_range(value: str, minimum: int, maximum: int) -> bool:
    try:
        parsed = int(value)
    except ValueError:
        return False
    return minimum <= parsed <= maximum


def invalid_reason(name: str, value: str, values: dict[str, str]) -> str | None:
    if _has_placeholder(value):
        return "contains a placeholder"
    if not value:
        return "is required" if _required(name, values) else None
    if "\n" in value or "\r" in value:
        return "must be a single line"
    if name in CHOICES and value not in CHOICES[name]:
        return "must be one of: " + ", ".join(sorted(choice or "<blank>" for choice in CHOICES[name]))
    if name == "COMPOSE_PROJECT_NAME" and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        return "must use lowercase letters, digits, hyphens, or underscores"
    if name in PORT_NAMES:
        if not _integer_in_range(value, 1, 65535):
            return "must be an integer from 1 to 65535"
        duplicates = [other for other in PORT_NAMES if other != name and values.get(other) == value]
        if duplicates:
            return f"duplicates {sorted(duplicates)[0]}"
    if name in SECRET_NAMES:
        if len(value) < 32:
            return "must contain at least 32 characters"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            return "must be URL-safe (letters, digits, underscore, and hyphen)"
        duplicates = [other for other in SECRET_NAMES if other != name and values.get(other) == value]
        if duplicates:
            return f"must not reuse {sorted(duplicates)[0]}"
    if name in API_KEY_NAMES and (not value.strip() or any(character.isspace() for character in value)):
        return "must be nonempty and contain no whitespace"
    if name in UUID_NAMES:
        try:
            parsed_uuid = UUID(value)
        except ValueError:
            return "must be a valid UUID"
        if parsed_uuid.version != 4:
            return "must be a UUID v4"
        other = "GATEWAY_DEMO_USER_B_ID" if name.endswith("_A_ID") else "GATEWAY_DEMO_USER_A_ID"
        if values.get(other) == value:
            return f"must be distinct from {other}"
    if name in PASSWORD_HASH_NAMES:
        match = PASSWORD_HASH_PATTERN.fullmatch(value)
        if not match or int(match.group(1)) < 210_000:
            return "must be a valid PBKDF2-SHA256 hash with at least 210000 iterations"
    if name in INTEGER_RANGES:
        minimum, maximum = INTEGER_RANGES[name]
        if not _integer_in_range(value, minimum, maximum):
            return f"must be an integer from {minimum} to {maximum}"
    if name == "GI_LLM_TIMEOUT_S":
        try:
            timeout = float(value)
        except ValueError:
            return "must be a number from 1 to 120"
        if not 1 <= timeout <= 120:
            return "must be a number from 1 to 120"
    if name == "GOAL_MANAGEMENT_INTERNAL_TIMEOUT_SECONDS":
        try:
            timeout = float(value)
        except ValueError:
            return "must be a number from 0.1 to 30"
        if not 0.1 <= timeout <= 30:
            return "must be a number from 0.1 to 30"
    if name in {"GATEWAY_JWT_ISSUER", "GATEWAY_JWT_AUDIENCE", "SERVICE_JWT_ISSUER",
                "SERVICE_JWT_AUDIENCE", "POSTGRES_USER", "RABBITMQ_DEFAULT_USER"}:
        if not value.strip() or any(character.isspace() for character in value):
            return "must be nonempty and contain no whitespace"
    if name in {"NEXT_PUBLIC_GATEWAY_URL", "GATEWAY_DEMO_ALLOWED_ORIGIN"}:
        if not _origin(value, https_only=values.get("GOALPULSE_PROFILE") == "demo"):
            return "must be one valid HTTP(S) origin; demo requires HTTPS"
        counterpart = (
            "GATEWAY_DEMO_ALLOWED_ORIGIN"
            if name == "NEXT_PUBLIC_GATEWAY_URL"
            else "NEXT_PUBLIC_GATEWAY_URL"
        )
        if values.get("GOALPULSE_PROFILE") == "demo" and values.get(counterpart, "").rstrip("/") != value.rstrip("/"):
            return f"must match {counterpart} in demo"
    if name == "CONTEXT_DEV_ACCESS_POLICY_IDS":
        policy_ids = [item.strip() for item in value.split(",") if item.strip()]
        if (not policy_ids or len(policy_ids) > 16 or len(policy_ids) != len(set(policy_ids))
                or any(not re.fullmatch(r"pol_[A-Za-z0-9_-]{1,196}", item) for item in policy_ids)):
            return "must contain one to 16 unique provider-issued pol_ IDs"
    if name == "DM_OPERATOR_IDS":
        for item in [part.strip() for part in value.split(",") if part.strip()]:
            try:
                UUID(item)
            except ValueError:
                return "must be blank or contain comma-separated UUIDs"
    if name == "DE_MERCHANT_HOSTS":
        hosts = [item.strip() for item in value.split(",") if item.strip()]
        if not hosts or any(
            not HOST_PATTERN.fullmatch(host)
            or host.lower().endswith((".localhost", ".local", ".internal", ".localdomain"))
            for host in hosts
        ):
            return "must contain comma-separated public hostnames"
    if name == "GI_LLM_PROVIDER" and values.get("GOALPULSE_PROFILE") == "demo" and value != "anthropic":
        return "demo requires anthropic"
    if name == "NEXT_PUBLIC_API_MODE" and values.get("GOALPULSE_PROFILE") == "demo" and value != "gateway":
        return "demo requires gateway"
    if name in {"DE_EVIDENCE_MODE", "DE_EVIDENCE_PROVIDER"}:
        if values.get("GOALPULSE_PROFILE") != "test" and value != "real":
            return "non-test runtime requires real"
    if name == "DE_ALLOW_FIXTURE" and values.get("GOALPULSE_PROFILE") != "test" and value != "false":
        return "non-test runtime requires false"
    if name == "GATEWAY_DEMO_USER_A_USERNAME" and value == values.get("GATEWAY_DEMO_USER_B_USERNAME"):
        return "must be distinct from demo user B"
    if name == "GATEWAY_DEMO_USER_B_USERNAME" and value == values.get("GATEWAY_DEMO_USER_A_USERNAME"):
        return "must be distinct from demo user A"
    return None


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def password_hash(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    iterations = 310_000
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def _sensitive(name: str) -> bool:
    return name in SECRET_NAMES | API_KEY_NAMES | PASSWORD_HASH_NAMES


def _generated(name: str, getpass_fn: Callable[[str], str]) -> str:
    if name in SECRET_NAMES:
        return secrets.token_urlsafe(48)
    if name in UUID_NAMES:
        return str(uuid4())
    if name in PASSWORD_HASH_NAMES:
        first = getpass_fn("  New demo password (hidden): ")
        second = getpass_fn("  Confirm demo password (hidden): ")
        if first != second:
            raise ValueError("password confirmation did not match")
        return password_hash(first)
    raise ValueError("automatic generation is not available")


def prompt_field(
    field: Field,
    current: str,
    values: dict[str, str],
    *,
    input_fn: Callable[[str], str] = input,
    getpass_fn: Callable[[str], str] = getpass.getpass,
) -> PromptResult:
    reason = invalid_reason(field.name, current, values)
    print(f"\n{field.name}")
    print(f"  Description: {field.description}")
    print(f"  Options: {field.options}")
    print("  Current: [configured secret]" if _sensitive(field.name) and current and not _has_placeholder(current)
          else f"  Current: {current or '[empty]'}")
    if reason:
        print(f"  Problem: {reason}")
    controls = "Enter keeps current; s skips; - clears"
    if field.name in GENERATABLE_NAMES:
        controls += "; g generates"
    print(f"  Controls: {controls}")
    reader = getpass_fn if _sensitive(field.name) else input_fn
    raw = reader("  New value: ").strip()
    if not raw:
        return PromptResult(current)
    if raw.lower() == "s":
        return PromptResult(current, skipped=True)
    if raw == "-":
        return PromptResult("")
    if raw.lower() == "g" and field.name in GENERATABLE_NAMES:
        return PromptResult(_generated(field.name, getpass_fn))
    return PromptResult(raw)


def generate_random_values(
    fields: list[Field],
    values: dict[str, str],
    selected: set[str],
) -> list[str]:
    """Replace invalid random credentials/UUIDs without displaying their values."""
    generated: list[str] = []
    for field in fields:
        if field.name not in RANDOM_GENERATABLE_NAMES:
            continue
        if selected and field.name not in selected:
            continue
        if invalid_reason(field.name, values.get(field.name, ""), values) is None:
            continue
        values[field.name] = _generated(field.name, getpass.getpass)
        generated.append(field.name)
    return generated


def render_template(lines: list[str], values: dict[str, str], known_names: set[str]) -> str:
    rendered: list[str] = []
    for line in lines:
        if "=" in line and not line.startswith("#"):
            name = line.split("=", 1)[0]
            if name in known_names:
                rendered.append(f"{name}={values.get(name, '')}")
                continue
        rendered.append(line)
    extras = sorted(set(values) - known_names)
    if extras:
        rendered.extend([
            "",
            "# -----------------------------------------------------------------------------",
            "# Preserved legacy/custom variables not consumed by the current Compose file",
            "# -----------------------------------------------------------------------------",
        ])
        for name in extras:
            rendered.append(f"{name}={values[name]}")
    return "\n".join(rendered) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    finally:
        if temporary.exists():
            temporary.unlink()


def _redacted_status(fields: list[Field], values: dict[str, str]) -> tuple[int, int]:
    invalid = 0
    for field in fields:
        reason = invalid_reason(field.name, values.get(field.name, ""), values)
        if reason:
            invalid += 1
            print(f"INVALID {field.name}: {reason}")
    valid = len(fields) - invalid
    print(f"STATUS {valid} valid, {invalid} invalid; values were not displayed")
    return valid, invalid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--all", action="store_true", help="Prompt for every variable, including valid values")
    parser.add_argument("--only", action="append", default=[], metavar="NAME", help="Prompt only for NAME; repeatable")
    parser.add_argument(
        "--generate-random",
        action="store_true",
        help="Generate invalid/placeholder secrets and UUIDs before prompting",
    )
    parser.add_argument("--check", action="store_true", help="Validate without prompting or writing")
    args = parser.parse_args()

    try:
        lines, fields = parse_template(args.template)
    except (OSError, ValueError) as error:
        print(f"FAIL template: {error}")
        return 1
    known_names = {field.name for field in fields}
    unknown_selection = sorted(set(args.only) - known_names)
    if unknown_selection:
        print("FAIL unknown variable(s): " + ", ".join(unknown_selection))
        return 1

    values = {field.name: field.default for field in fields}
    if args.env_file.exists():
        existing, errors = parse_env(args.env_file)
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        values.update(existing)

    if args.check:
        _valid, invalid = _redacted_status(fields, values)
        full_errors = validate(values, args.compose_file)
        if args.env_file.exists() and stat.S_IMODE(args.env_file.stat().st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
            full_errors.append("environment file permissions must be 0600")
        if full_errors:
            print(f"FAIL full environment validation found {len(full_errors)} problem(s); values were not displayed")
            return 1
        if invalid:
            return 1
        print("PASS environment is ready; values were not displayed")
        return 0

    selected = set(args.only)
    random_generated = (
        generate_random_values(fields, values, selected)
        if args.generate_random
        else []
    )
    if random_generated:
        print("GENERATED " + ", ".join(random_generated) + "; values were not displayed")
    prompted = 0
    skipped: list[str] = []
    try:
        for field in fields:
            current = values.get(field.name, "")
            if field.name in random_generated and not args.all:
                continue
            should_prompt = (
                (field.name in selected if selected else False)
                or (args.all and not selected)
                or (not args.all and not selected and invalid_reason(field.name, current, values) is not None)
            )
            if not should_prompt:
                continue
            prompted += 1
            result = prompt_field(field, current, values)
            values[field.name] = result.value
            if result.skipped:
                skipped.append(field.name)
    except (EOFError, KeyboardInterrupt):
        print("\nCANCELLED no changes were written")
        return 130
    except ValueError as error:
        print(f"FAIL {error}; no changes were written")
        return 1

    atomic_write(args.env_file, render_template(lines, values, known_names))
    print(f"WROTE {args.env_file} with mode 0600; {prompted} variable(s) prompted")
    if skipped:
        print("SKIPPED " + ", ".join(skipped))
    _valid, invalid = _redacted_status(fields, values)
    full_errors = validate(values, args.compose_file)
    if invalid or full_errors:
        print("INCOMPLETE rerun the manager to finish skipped or invalid variables")
        return 2
    print("PASS environment is ready for quiet Compose validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
