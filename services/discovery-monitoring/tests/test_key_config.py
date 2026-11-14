"""Key configuration tests use temporary files, synthetic keys and fake providers."""

from datetime import datetime, timezone
import json
from unittest.mock import Mock

import pytest

from goalpulse_discovery import config, probe
from goalpulse_discovery.config import ConfigurationError, KEY_PLACEHOLDER, read_context_api_key
from goalpulse_discovery.context_provider import Output, ScrapeResult


def test_environment_wins_over_local_file_without_reading_it(tmp_path):
    path = tmp_path / ".env"
    path.write_text("CONTEXT_DEV_API_KEY=malformed value", encoding="utf-8")
    assert read_context_api_key(environ={"CONTEXT_DEV_API_KEY": "fixture-environment-key"}, env_file=path) == "fixture-environment-key"
    assert read_context_api_key(environ={"CONTEXT_DEV_API_KEY": ""}, env_file=path) is None


@pytest.mark.parametrize("assignment", [
    "CONTEXT_DEV_API_KEY=fixture-local-key",
    "CONTEXT_DEV_API_KEY='fixture-local-key'",
    'CONTEXT_DEV_API_KEY="fixture-local-key"',
    "  CONTEXT_DEV_API_KEY = fixture-local-key  ",
])
def test_manual_local_file_loads_without_changing_process_environment(tmp_path, assignment):
    path = tmp_path / ".env"
    path.write_text("# Separate-line comment\n\n" + assignment + "\n", encoding="utf-8-sig")
    environment = {}
    assert read_context_api_key(environ=environment, env_file=path) == "fixture-local-key"
    assert environment == {}


def test_missing_blank_and_placeholder_keys_are_unconfigured(tmp_path):
    path = tmp_path / ".env"
    assert read_context_api_key(environ={}, env_file=path) is None
    for value in ("", KEY_PLACEHOLDER):
        path.write_text(f"CONTEXT_DEV_API_KEY={value}\n", encoding="utf-8")
        assert read_context_api_key(environ={}, env_file=path) is None


@pytest.mark.parametrize("assignment", [
    "CONTEXT_DEV_API_KEY='fixture-secret",
    "CONTEXT_DEV_API_KEY=fixture-secret with spaces",
    "CONTEXT_DEV_API_KEY",
    "CONTEXT_DEV_API_KEY=fixture-secret\nCONTEXT_DEV_API_KEY=another-secret",
])
def test_bad_configuration_is_rejected_without_echoing_secrets(tmp_path, assignment):
    path = tmp_path / ".env"
    path.write_text(assignment, encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        read_context_api_key(environ={}, env_file=path)
    assert "fixture-secret" not in str(error.value)
    assert "another-secret" not in str(error.value)


def test_probe_uses_local_key_with_fake_provider_and_does_not_print_it(tmp_path, monkeypatch, capsys):
    path = tmp_path / ".env"
    path.write_text("CONTEXT_DEV_API_KEY=fixture-private-key\n", encoding="utf-8")
    monkeypatch.delenv("CONTEXT_DEV_API_KEY", raising=False)
    monkeypatch.setattr(config, "LOCAL_ENV_FILE", path)
    monkeypatch.setattr("sys.argv", ["probe", "https://example.com/item"])
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    result = ScrapeResult("https://example.com/item", None, now, 0, now,
        Output(True, {"isProductPage": True, "product": {"name": "Synthetic fixture", "price": "1.00"}}),
        Output(True, "Synthetic fixture"))
    fake = Mock()
    fake.scrape.return_value = result
    factory = Mock(return_value=fake)
    monkeypatch.setattr(probe, "ContextProvider", factory)
    assert probe.main() == 0
    factory.assert_called_once_with("fixture-private-key")
    assert fake.scrape.call_count == 2
    output = capsys.readouterr().out
    assert "fixture-private-key" not in output
    assert json.loads(output)["status"] == "completed"


def test_probe_configuration_error_returns_not_run_without_provider_call(tmp_path, monkeypatch, capsys):
    path = tmp_path / ".env"
    path.write_text("CONTEXT_DEV_API_KEY='fixture-private-key\n", encoding="utf-8")
    monkeypatch.delenv("CONTEXT_DEV_API_KEY", raising=False)
    monkeypatch.setattr(config, "LOCAL_ENV_FILE", path)
    monkeypatch.setattr("sys.argv", ["probe", "https://example.com/item"])
    factory = Mock()
    monkeypatch.setattr(probe, "ContextProvider", factory)
    assert probe.main() == 2
    factory.assert_not_called()
    output = capsys.readouterr().out
    assert "fixture-private-key" not in output
    assert json.loads(output)["status"] == "NOT RUN"
