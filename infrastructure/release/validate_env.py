#!/usr/bin/env python3
"""Validate GoalPulse's centralized environment without displaying values."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = ROOT / ".env"
DEFAULT_COMPOSE = ROOT / "infrastructure" / "docker-compose.yml"
PLACEHOLDER_MARKERS = ("CHANGE_ME", "change_this_")
SECRET_NAMES = (
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
)
PORT_NAMES = (
    "FRONTEND_HOST_PORT",
    "GATEWAY_HOST_PORT",
    "POSTGRES_HOST_PORT",
    "RABBITMQ_HOST_PORT",
    "RABBITMQ_MANAGEMENT_HOST_PORT",
)


def parse_env(path: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    errors: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append(f"line {line_number} is not NAME=VALUE")
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            errors.append(f"line {line_number} has an invalid variable name")
            continue
        if name in values:
            errors.append(f"{name} is defined more than once")
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = re.sub(r"\\(['\\])", r"\1", value[1:-1])
        elif len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        values[name] = value
    return values, errors


def compose_env_file_errors(path: Path) -> list[str]:
    """Reject dollar values that Compose would treat as nested interpolation."""
    errors: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        raw_value = raw_value.strip()
        single_quoted = len(raw_value) >= 2 and raw_value[0] == raw_value[-1] == "'"
        if "$" in raw_value and not single_quoted:
            errors.append(
                f"{name.strip()} contains an unquoted dollar sign on line {line_number}; "
                "rerun manage_env.py to make it Compose-safe"
            )
    return errors


def compose_variables(path: Path) -> set[str]:
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", path.read_text(encoding="utf-8")))


def is_origin(value: str, *, require_https: bool) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _port = parsed.port
    except ValueError:
        return False
    allowed_schemes = {"https"} if require_https else {"http", "https"}
    return (
        parsed.scheme in allowed_schemes
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def validate(values: dict[str, str], compose_path: Path) -> list[str]:
    errors: list[str] = []
    required = compose_variables(compose_path)
    missing = sorted(required - values.keys())
    if missing:
        errors.append("missing Compose variables: " + ", ".join(missing))

    for name, value in values.items():
        if any(marker in value for marker in PLACEHOLDER_MARKERS):
            errors.append(f"{name} still contains a placeholder")

    for name in SECRET_NAMES:
        if not values.get(name):
            errors.append(f"{name} must not be empty")
        elif len(values[name]) < 32:
            errors.append(f"{name} must contain at least 32 characters")
    populated_secrets = [values.get(name, "") for name in SECRET_NAMES]
    if len([value for value in populated_secrets if value]) != len(set(value for value in populated_secrets if value)):
        errors.append("database, broker, and JWT secrets must use independent values")

    ports: dict[int, str] = {}
    for name in PORT_NAMES:
        raw_value = values.get(name, "")
        try:
            port = int(raw_value)
        except ValueError:
            errors.append(f"{name} must be an integer between 1 and 65535")
            continue
        if not 1 <= port <= 65535:
            errors.append(f"{name} must be an integer between 1 and 65535")
        elif port in ports:
            errors.append(f"{name} duplicates the host port used by {ports[port]}")
        else:
            ports[port] = name

    profile = values.get("GOALPULSE_PROFILE", "")
    if profile not in {"test", "dev", "demo"}:
        errors.append("GOALPULSE_PROFILE must be test, dev, or demo")

    api_mode = values.get("NEXT_PUBLIC_API_MODE", "")
    if api_mode not in {"mock", "gateway"}:
        errors.append("NEXT_PUBLIC_API_MODE must be mock or gateway")

    context_without_policy = values.get("DM_ALLOW_CONTEXT_WITHOUT_ACCESS_POLICY", "false").lower()
    if context_without_policy not in {"true", "false"}:
        errors.append("DM_ALLOW_CONTEXT_WITHOUT_ACCESS_POLICY must be true or false")

    merchant_hosts = [item.strip() for item in values.get("DE_MERCHANT_HOSTS", "").split(",") if item.strip()]
    if not merchant_hosts:
        errors.append("DE_MERCHANT_HOSTS must contain at least one approved hostname")
    for host in merchant_hosts:
        if not re.fullmatch(
            r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
            r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
            host,
        ) or host.lower().endswith((".localhost", ".local", ".internal", ".localdomain")):
            errors.append("DE_MERCHANT_HOSTS entries must be public hostnames without a scheme or path")
            break

    if profile == "demo":
        if api_mode != "gateway":
            errors.append("demo requires NEXT_PUBLIC_API_MODE=gateway")
        public_url = values.get("NEXT_PUBLIC_GATEWAY_URL", "")
        allowed_origin = values.get("GATEWAY_DEMO_ALLOWED_ORIGIN", "")
        if not is_origin(public_url, require_https=True):
            errors.append("demo NEXT_PUBLIC_GATEWAY_URL must be one HTTPS origin")
        if not is_origin(allowed_origin, require_https=True):
            errors.append("demo GATEWAY_DEMO_ALLOWED_ORIGIN must be one HTTPS origin")
        if public_url.rstrip("/") != allowed_origin.rstrip("/"):
            errors.append("NEXT_PUBLIC_GATEWAY_URL and GATEWAY_DEMO_ALLOWED_ORIGIN must match")
        if values.get("GI_LLM_PROVIDER") != "anthropic":
            errors.append("demo requires GI_LLM_PROVIDER=anthropic")
        if not values.get("GI_LLM_MODEL"):
            errors.append("demo requires GI_LLM_MODEL")
        if not values.get("ANTHROPIC_API_KEY"):
            errors.append("demo requires ANTHROPIC_API_KEY")
        if values.get("DM_PROVIDER") != "context_dev":
            errors.append("demo requires DM_PROVIDER=context_dev")
        if not values.get("CONTEXT_DEV_API_KEY"):
            errors.append("demo requires CONTEXT_DEV_API_KEY")
        policy_ids = [item.strip() for item in values.get("CONTEXT_DEV_ACCESS_POLICY_IDS", "").split(",") if item.strip()]
        if ((not policy_ids and context_without_policy != "true")
                or any(not re.fullmatch(r"pol_[A-Za-z0-9_-]+", item) for item in policy_ids)):
            errors.append(
                "demo requires provider-issued CONTEXT_DEV_ACCESS_POLICY_IDS beginning with pol_ "
                "or DM_ALLOW_CONTEXT_WITHOUT_ACCESS_POLICY=true"
            )
        if values.get("DE_EVIDENCE_MODE") != "real":
            errors.append("demo requires DE_EVIDENCE_MODE=real")
        if values.get("DE_EVIDENCE_PROVIDER") != "real":
            errors.append("demo requires DE_EVIDENCE_PROVIDER=real")
        if values.get("DE_ALLOW_FIXTURE", "").lower() != "false":
            errors.append("demo requires DE_ALLOW_FIXTURE=false")
        session_secret = values.get("GATEWAY_DEMO_SESSION_SECRET", "")
        if len(session_secret) < 32:
            errors.append("demo GATEWAY_DEMO_SESSION_SECRET must contain at least 32 characters")
        for prefix in ("GATEWAY_DEMO_USER_A", "GATEWAY_DEMO_USER_B"):
            for suffix in ("USERNAME", "DISPLAY_NAME"):
                if not values.get(f"{prefix}_{suffix}"):
                    errors.append(f"demo requires {prefix}_{suffix}")
            try:
                UUID(values.get(f"{prefix}_ID", ""))
            except ValueError:
                errors.append(f"demo requires a valid UUID in {prefix}_ID")
            password_hash = values.get(f"{prefix}_PASSWORD_HASH", "")
            hash_match = re.fullmatch(r"pbkdf2_sha256\$(\d+)\$[A-Za-z0-9_-]+\$[A-Za-z0-9_-]+", password_hash)
            if not hash_match or int(hash_match.group(1)) < 210_000:
                errors.append(f"demo requires a generated pbkdf2_sha256 hash in {prefix}_PASSWORD_HASH")
        if values.get("GATEWAY_DEMO_USER_A_ID") == values.get("GATEWAY_DEMO_USER_B_ID"):
            errors.append("demo user IDs must be distinct")
        if values.get("GATEWAY_DEMO_USER_A_USERNAME") == values.get("GATEWAY_DEMO_USER_B_USERNAME"):
            errors.append("demo usernames must be distinct")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE)
    args = parser.parse_args()
    if not args.env_file.is_file():
        print(f"FAIL environment file does not exist: {args.env_file}")
        return 1
    if not args.compose_file.is_file():
        print(f"FAIL Compose file does not exist: {args.compose_file}")
        return 1

    values, errors = parse_env(args.env_file)
    errors.extend(compose_env_file_errors(args.env_file))
    errors.extend(validate(values, args.compose_file))
    mode = stat.S_IMODE(args.env_file.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        errors.append("environment file permissions must be 0600")

    if errors:
        for error in errors:
            print(f"FAIL {error}")
        print(f"FAIL centralized environment validation found {len(errors)} problem(s); values were not displayed")
        return 1
    print(f"PASS centralized environment validation ({len(values)} variables; values not displayed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
