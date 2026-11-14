# D3-DM-01 local key configuration — 2026-11-14

Implemented an editable local key file for the opt-in Context.dev probe.

- `goalpulse_discovery/config.py` reads only `CONTEXT_DEV_API_KEY`. The process
  environment takes precedence over the service-local `.env`; placeholder/blank
  values remain unconfigured. File contents are not executed, expanded or logged.
- `goalpulse_discovery/probe.py` uses the loader and reports invalid configuration
  as NOT RUN without creating a provider client or exposing the value.
- `.env.example` is a tracked placeholder template. The ignored local `.env` was
  created with a placeholder for manual editing; the real key was not supplied.
- `README.md` documents the exact edit/run steps. `tests/test_key_config.py` covers
  file loading, quoting/BOM, environment precedence, malformed/duplicate settings,
  redaction and the probe through a fake provider. The existing missing-key test
  now isolates the local file so a developer's real configuration cannot trigger
  network calls during the offline suite.

Contracts, public/internal HTTP, event routing, databases and dependencies are
unchanged. No new environment variable or package was introduced. Only the probe
loads this file automatically; future runtime entrypoints can use the same loader
when they are implemented under their approved contracts.

Executed from repository root using `.venv/Scripts/python.exe`:

- `scripts/validate_contracts.py`: PASS.
- Current merged service follow-up: `-m pytest -q` is **233 passed, 7 skipped**
  (the missing PostgreSQL DSNs remain explicit skips).
- `git diff --check`: PASS.
- `git check-ignore -v -- services/discovery-monitoring/.env`: PASS; root ignore
  rule excludes the local file. It is not staged/tracked.

Live provider acceptance: **NOT RUN**; no real key configured and this change made
no live request. Replace the placeholder locally, then run the documented opt-in
probe with a permitted public product URL. Other Developer 3 acceptance gates in
the current-status report remain open.
