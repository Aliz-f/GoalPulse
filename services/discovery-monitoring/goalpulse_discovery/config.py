"""Read the provider key from environment or the service-local ignored .env file.

Only CONTEXT_DEV_API_KEY is read. File contents are never executed, expanded,
logged, or copied into the process environment. No dotenv dependency is needed.
"""

from collections.abc import Mapping
import os
from pathlib import Path

KEY_NAME = "CONTEXT_DEV_API_KEY"
KEY_PLACEHOLDER = "replace_with_your_context_dev_api_key"
LOCAL_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class ConfigurationError(ValueError):
    """Fixed messages only: never include the configured secret or file contents."""


def _validated_key(value: str) -> str | None:
    value = value.strip()
    if not value or value == KEY_PLACEHOLDER:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigurationError("CONTEXT_DEV_API_KEY must be a single token without whitespace")
    return value


def read_context_api_key(*, environ: Mapping[str, str] | None = None,
                         env_file: Path | None = None) -> str | None:
    """Environment takes precedence, including an explicitly blank value.

    The local file supports KEY=value or KEY='value'/KEY="value", blank lines,
    comments on separate lines and UTF-8 with an optional Windows BOM. It is a
    deliberately narrow configuration format, not a general dotenv/shell parser.
    """
    environment = os.environ if environ is None else environ
    if KEY_NAME in environment:
        return _validated_key(environment[KEY_NAME])
    path = LOCAL_ENV_FILE if env_file is None else env_file
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        raise ConfigurationError("Cannot read the Discovery & Monitoring .env file as UTF-8") from None
    found = False
    result = None
    for line in text.splitlines():
        name, separator, value = line.strip().partition("=")
        if name.strip() != KEY_NAME:
            continue
        if found or not separator:
            raise ConfigurationError("Use exactly one CONTEXT_DEV_API_KEY=value assignment in the .env file")
        found = True
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigurationError("CONTEXT_DEV_API_KEY has unmatched quotes in the .env file")
            value = value[1:-1]
        result = _validated_key(value)
    return result
