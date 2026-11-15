from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import stat
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infrastructure" / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"
VALIDATOR = ROOT / "infrastructure" / "release" / "validate_env.py"
NGINX = ROOT / "infrastructure" / "nginx" / "goalpulse.conf.example"
ROOT_DOCKERIGNORE = ROOT / ".dockerignore"


def env_names(path: Path) -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }


def validator_module():
    spec = importlib.util.spec_from_file_location("goalpulse_validate_env", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_central_env_template_exactly_covers_compose_inputs():
    compose_names = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", COMPOSE.read_text(encoding="utf-8")))
    assert env_names(ENV_EXAMPLE) == compose_names


def test_env_template_is_domain_grouped_and_contains_no_real_secrets():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for domain in (
        "Deployment identity",
        "Frontend",
        "PostgreSQL",
        "RabbitMQ",
        "Gateway authentication",
        "Goal Intelligence",
        "Goal Management",
        "Discovery Monitoring",
        "Data & Evidence",
        "Notification",
    ):
        assert domain in text
    assert "sk-ant-" not in text
    assert re.search(r"^CONTEXT_DEV_API_KEY=CHANGE_ME_", text, re.MULTILINE)
    assert re.search(r"^CONTEXT_DEV_ACCESS_POLICY_IDS=CHANGE_ME_", text, re.MULTILINE)


def test_compose_has_no_nginx_service_and_frontend_build_receives_no_secret():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert "nginx" not in compose["services"]
    frontend = compose["services"]["frontend"]
    assert frontend["ports"] == ["127.0.0.1:${FRONTEND_HOST_PORT:-3000}:3000"]
    assert set(frontend["build"]["args"]) == {
        "NEXT_PUBLIC_GOALPULSE_PROFILE",
        "NEXT_PUBLIC_API_MODE",
        "NEXT_PUBLIC_GATEWAY_URL",
    }
    assert not any("SECRET" in name or "KEY" in name or "PASSWORD" in name for name in frontend["build"]["args"])


def test_host_nginx_routes_public_api_and_frontend_to_loopback_only():
    text = NGINX.read_text(encoding="utf-8")
    assert "server 127.0.0.1:3000;" in text
    assert "server 127.0.0.1:8000;" in text
    assert "location ~ ^/(v1|v2|health)(/|$)" in text
    assert "proxy_pass http://goalpulse_gateway;" in text
    assert "proxy_pass http://goalpulse_frontend;" in text
    assert "ssl_certificate /etc/letsencrypt/live/goalpulse.example.com/fullchain.pem;" in text
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in text


def test_root_docker_build_context_excludes_secrets_and_local_artifacts():
    ignored = set(ROOT_DOCKERIGNORE.read_text(encoding="utf-8").splitlines())
    for required in (".git", ".env", ".env.*", ".venv", "venv", "**/node_modules", "frontend/.next"):
        assert required in ignored
    assert "!.env.example" in ignored


def test_demo_validator_rejects_missing_context_policy_and_mock_runtime(tmp_path):
    module = validator_module()
    values, parse_errors = module.parse_env(ENV_EXAMPLE)
    assert not parse_errors
    values = {name: value.replace("CHANGE_ME", "configured") for name, value in values.items()}
    values["NEXT_PUBLIC_API_MODE"] = "mock"
    values["CONTEXT_DEV_ACCESS_POLICY_IDS"] = ""
    errors = module.validate(values, COMPOSE)
    assert "demo requires NEXT_PUBLIC_API_MODE=gateway" in errors
    assert any("CONTEXT_DEV_ACCESS_POLICY_IDS" in error for error in errors)


def test_env_validator_never_displays_values_on_failure(tmp_path):
    env_file = tmp_path / ".env"
    secret_value = "this-value-must-never-be-printed"
    env_file.write_text(f"POSTGRES_PASSWORD={secret_value}\n", encoding="utf-8")
    env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--env-file", str(env_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert secret_value not in result.stdout
    assert "values were not displayed" in result.stdout
