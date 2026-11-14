"""Separate private HTTP, activation consumer and durable-job worker processes."""

import argparse
import time
import os
import json
from uuid import UUID

from .runtime import from_env, open_broker
from .operator import require_local_operator


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GoalPulse Discovery monitoring runtime")
    parser.add_argument("mode", choices=("api", "consume", "worker", "scheduler", "check-now", "schedule-status", "ready"))
    parser.add_argument("--once", action="store_true", help="process one due job or activation")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--goal-id", type=UUID)
    parser.add_argument("--request-id", type=UUID, help="stable check-now idempotency UUID")
    args = parser.parse_args(argv)
    runtime = None
    try:
        if args.mode in ("check-now", "schedule-status"):
            require_local_operator(os.environ.get("DM_OPERATOR_IDS", ""))
            if args.goal_id is None or (args.mode == "check-now" and args.request_id is None):
                parser.error("operator commands require --goal-id; check-now also requires --request-id")
        runtime = from_env()
        if args.mode == "api":
            import uvicorn
            uvicorn.run(runtime.app, host=args.host, port=args.port, log_level="warning", access_log=False)
        elif args.mode == "worker":
            while True:
                result = runtime.process_once()
                print(json.dumps({"discovery_job": result, "reason": runtime.last_result}), flush=True)
                if args.once:
                    break
                time.sleep(1)
        elif args.mode == "scheduler":
            while True:
                print(json.dumps(runtime.scheduler.tick()), flush=True)
                if args.once:
                    break
                time.sleep(5)
        elif args.mode == "check-now":
            result = runtime.check_now(args.goal_id, args.request_id)
            print(json.dumps(result))
            return 0 if result["status"] in ("queued", "coalesced", "duplicate") else 1
        elif args.mode == "schedule-status":
            print(json.dumps(runtime.scans.status(args.goal_id), default=str))
        elif args.mode == "ready":
            ready = runtime.ready()
            print("discovery_ready=" + ("true" if ready else "false"))
            return 0 if ready else 1
        else:
            broker = open_broker(runtime.settings)
            try:
                channel = broker.channel()
                runtime.consumer.configure(channel, consume=not args.once)
                if args.once:
                    # --once is non-blocking when no event is queued.
                    from .consumer import ACTIVATED_QUEUE
                    method, properties, body = channel.basic_get(queue=ACTIVATED_QUEUE, auto_ack=False)
                    if method is not None:
                        runtime.consumer.on_message(channel, method, properties, body)
                else:
                    channel.start_consuming()
            finally:
                broker.close()
        return 0
    except KeyboardInterrupt:
        return 0
    except PermissionError:
        print("operator=forbidden")
        return 2
    except Exception:
        # Arbitrary dependency errors may include credentials, DSNs or page text.
        print("discovery_runtime=unavailable")
        return 1
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
