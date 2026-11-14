"""Local API and outbox process entrypoints; configuration values stay private."""

import argparse
import json
import time

from .runtime import RuntimeConfigurationError, RuntimeUnavailable, Settings, build_runtime, publish_once


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GoalPulse Data & Evidence runtime")
    parser.add_argument("command", choices=("api", "publisher"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8004, type=int)
    parser.add_argument("--once", action="store_true", help="publisher: process at most one due row")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        settings = Settings.from_env(publisher=args.command == "publisher")
        runtime = build_runtime(settings)
        if args.command == "api":
            import uvicorn

            uvicorn.run(runtime.app, host=args.host, port=args.port, access_log=False, log_level="warning")
            return 0
        delay = 1
        while True:
            try:
                status = publish_once(runtime)
                delay = 1
            except Exception:
                status = "unavailable"
                delay = min(delay * 2, 30)
            if status != "idle" or args.once:
                print(json.dumps({"component": "data-evidence-publisher", "status": status}))
            if args.once:
                return 0 if status in ("idle", "published") else 1
            time.sleep(delay)
    except (RuntimeConfigurationError, RuntimeUnavailable) as error:
        print(json.dumps({"component": "data-evidence", "status": "startup_failed", "reason": str(error)}))
        return 1
    except Exception:
        print(json.dumps({"component": "data-evidence", "status": "startup_failed",
                          "reason": "DATA_EVIDENCE_STARTUP_FAILED"}))
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
