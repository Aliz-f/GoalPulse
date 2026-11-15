from __future__ import annotations

from pathlib import Path
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "infrastructure" / "release"
ENV_EXAMPLE = ROOT / ".env.example"
MANAGER_SCRIPT = RELEASE / "manage_env.py"

sys.path.insert(0, str(RELEASE))
import manage_env  # noqa: E402


def test_every_template_variable_has_adjacent_description_and_options():
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    assignments = 0
    for index, line in enumerate(lines):
        if not line or line.startswith("#") or "=" not in line:
            continue
        assignments += 1
        assert index >= 2
        assert lines[index - 2].startswith("# Description: ")
        assert lines[index - 1].startswith("# Options: ")
        assert lines[index - 2].removeprefix("# Description: ").strip()
        assert lines[index - 1].removeprefix("# Options: ").strip()

    _template_lines, fields = manage_env.parse_template(ENV_EXAMPLE)
    assert assignments == len(fields)
    assert assignments > 0


def test_placeholder_is_invalid_and_user_can_skip_prompt():
    _lines, fields = manage_env.parse_template(ENV_EXAMPLE)
    field = next(item for item in fields if item.name == "GI_LLM_MODEL")
    values = {item.name: item.default for item in fields}

    assert manage_env.invalid_reason(field.name, field.default, values) == "contains a placeholder"
    result = manage_env.prompt_field(
        field,
        field.default,
        values,
        input_fn=lambda _prompt: "s",
    )

    assert result.skipped is True
    assert result.value == field.default


def test_policy_can_be_cleared_only_with_explicit_without_policy_override():
    _lines, fields = manage_env.parse_template(ENV_EXAMPLE)
    values = {item.name: item.default for item in fields}
    values["GOALPULSE_PROFILE"] = "demo"
    values["CONTEXT_DEV_ACCESS_POLICY_IDS"] = ""

    assert manage_env.invalid_reason("CONTEXT_DEV_ACCESS_POLICY_IDS", "", values) == "is required"

    values["DM_ALLOW_CONTEXT_WITHOUT_ACCESS_POLICY"] = "true"
    assert manage_env.invalid_reason("CONTEXT_DEV_ACCESS_POLICY_IDS", "", values) is None


def test_manager_generates_valid_secrets_uuid_and_password_hash():
    secret = manage_env._generated("POSTGRES_PASSWORD", lambda _prompt: "unused")
    assert manage_env.invalid_reason("POSTGRES_PASSWORD", secret, {"POSTGRES_PASSWORD": secret}) is None

    user_id = manage_env._generated("GATEWAY_DEMO_USER_A_ID", lambda _prompt: "unused")
    assert manage_env.invalid_reason("GATEWAY_DEMO_USER_A_ID", user_id, {"GATEWAY_DEMO_USER_A_ID": user_id}) is None

    answers = iter(("a long unique demo password", "a long unique demo password"))
    password_hash = manage_env._generated(
        "GATEWAY_DEMO_USER_A_PASSWORD_HASH",
        lambda _prompt: next(answers),
    )
    assert password_hash.startswith("pbkdf2_sha256$310000$")
    assert manage_env.invalid_reason(
        "GATEWAY_DEMO_USER_A_PASSWORD_HASH",
        password_hash,
        {"GATEWAY_DEMO_USER_A_PASSWORD_HASH": password_hash},
    ) is None


def test_manager_quotes_password_hash_dollars_for_compose_and_round_trips(tmp_path):
    lines, fields = manage_env.parse_template(ENV_EXAMPLE)
    values = {field.name: field.default for field in fields}
    password_hash = manage_env.password_hash("unique demo password")
    values["GATEWAY_DEMO_USER_A_PASSWORD_HASH"] = password_hash
    env_file = tmp_path / ".env"

    env_file.write_text(
        manage_env.render_template(lines, values, {field.name for field in fields}),
        encoding="utf-8",
    )
    rendered_line = next(
        line
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("GATEWAY_DEMO_USER_A_PASSWORD_HASH=")
    )
    parsed, parse_errors = manage_env.parse_env(env_file)

    assert rendered_line == f"GATEWAY_DEMO_USER_A_PASSWORD_HASH='{password_hash}'"
    assert not parse_errors
    assert parsed["GATEWAY_DEMO_USER_A_PASSWORD_HASH"] == password_hash
    assert manage_env.compose_env_file_errors(env_file) == []


def test_validator_rejects_unquoted_dollar_values_without_displaying_them(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GATEWAY_DEMO_USER_A_PASSWORD_HASH=pbkdf2_sha256$310000$salt$digest\n",
        encoding="utf-8",
    )

    errors = manage_env.compose_env_file_errors(env_file)

    assert len(errors) == 1
    assert "GATEWAY_DEMO_USER_A_PASSWORD_HASH" in errors[0]
    assert "salt" not in errors[0]
    assert "digest" not in errors[0]


def test_bulk_generator_covers_every_random_env_value_without_overwriting_valid_values():
    _lines, fields = manage_env.parse_template(ENV_EXAMPLE)
    values = {field.name: field.default for field in fields}
    configured_secret = "already_configured_unique_secret_value_123456789"
    values["POSTGRES_PASSWORD"] = configured_secret
    expected_random_names = {
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
        "GATEWAY_DEMO_USER_A_ID",
        "GATEWAY_DEMO_USER_B_ID",
    }

    generated = manage_env.generate_random_values(fields, values, set())

    assert manage_env.RANDOM_GENERATABLE_NAMES == expected_random_names
    assert manage_env.GENERATABLE_NAMES == expected_random_names | manage_env.PASSWORD_HASH_NAMES
    assert set(generated) == expected_random_names - {"POSTGRES_PASSWORD"}
    assert values["POSTGRES_PASSWORD"] == configured_secret
    for name in generated:
        assert manage_env.invalid_reason(name, values[name], values) is None
    assert "ANTHROPIC_API_KEY" not in generated
    assert "CONTEXT_DEV_API_KEY" not in generated
    assert "CONTEXT_DEV_ACCESS_POLICY_IDS" not in generated


def test_manager_creates_mode_0600_env_and_records_skipped_variable(tmp_path):
    env_file = tmp_path / ".env"
    result = subprocess.run(
        [
            sys.executable,
            str(MANAGER_SCRIPT),
            "--env-file",
            str(env_file),
            "--only",
            "GI_LLM_MODEL",
        ],
        cwd=ROOT,
        input="s\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert env_file.is_file()
    assert stat.S_IMODE(env_file.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR
    assert "SKIPPED GI_LLM_MODEL" in result.stdout
    assert "INCOMPLETE" in result.stdout


def test_manager_check_never_displays_existing_secret(tmp_path):
    env_file = tmp_path / ".env"
    secret = "context-secret-that-must-never-appear-in-output"
    env_file.write_text(f"CONTEXT_DEV_API_KEY={secret}\n", encoding="utf-8")
    env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    result = subprocess.run(
        [sys.executable, str(MANAGER_SCRIPT), "--env-file", str(env_file), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert secret not in result.stdout
    assert "values were not displayed" in result.stdout
