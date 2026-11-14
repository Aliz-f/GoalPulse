"""Explicit offline adapters for the production activation pipeline tests."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx

from goalpulse_discovery.context_provider import HttpResponse


class RecordedProvider:
    """Recorded Context HTTP envelopes, consumed by the real ContextProvider."""

    def __init__(self, scrape, *, urls=None):
        self.scrape = deepcopy(scrape)
        self.urls = urls or [scrape["url"]]
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, json.loads(kwargs["body"])))
        if path == "/v1/web/search":
            body = {"results": [{"url": url} for url in self.urls],
                    "request_id": "recorded-search", "cache_metadata": {"age_ms": 0}}
        elif path == "/v1/web/scrape":
            body = self.scrape
        else:
            raise AssertionError("Unexpected provider operation")
        return HttpResponse(200, {}, json.dumps(body).encode())


class MemoryJobs:
    """Durable-store double: accepted jobs and completed state survive rebuilds."""

    def __init__(self, trace):
        self.events, self.jobs, self.trace = {}, {}, trace
        self.fail_accept = False

    def accept(self, event):
        if self.fail_accept:
            raise OSError("Recorded database unavailable")
        key = event["event_id"]
        if key in self.events:
            if self.events[key] != event:
                raise ValueError("Reused event ID")
            self.trace.append("duplicate_committed")
            return False
        self.events[key] = deepcopy(event)
        payload = event["payload"]
        self.jobs.setdefault((payload["goal_id"], payload["goal_version"]),
                             {"event": deepcopy(event), "status": "pending", "attempts": 0})
        self.trace.append("activation_committed")
        return True

    def run_once(self, worker):
        job = next((job for job in self.jobs.values() if job["status"] == "pending"), None)
        if job is None:
            return "idle"
        event = job["event"]
        job["attempts"] += 1
        try:
            job["result"] = worker.run(UUID(event["payload"]["goal_id"]),
                                        event["payload"]["goal_version"], UUID(event["correlation_id"]))
        except Exception:
            # Production DiscoveryJobs also retains dependency failures for retry.
            if job["attempts"] >= 5:
                job["status"] = "dead"
        else:
            job["status"] = "done"
        return job["status"]


class MemoryUsage:
    def __init__(self):
        self.reservations, self.completions = [], []

    def reserve(self, resource, now, **kwargs):
        identifier = uuid4()
        self.reservations.append((identifier, resource, now, kwargs))
        return identifier

    def finish(self, identifier, **kwargs):
        self.completions.append((identifier, kwargs))


class MemoryJournal:
    def __init__(self):
        self.plans, self.records = {}, {}

    def load_plan(self, goal_id, version):
        return deepcopy(self.plans.get((goal_id, version)))

    def save_plan(self, goal_id, version, urls):
        return deepcopy(self.plans.setdefault((goal_id, version), deepcopy(urls)))

    def get(self, attempt_id):
        return deepcopy(self.records.get(str(attempt_id)))

    def put(self, submission, *, capture_metadata=None):
        key = submission["attempt_id"]
        if key in self.records and self.records[key]["submission"] != submission:
            raise ValueError("Attempt reused for different submission")
        return deepcopy(self.records.setdefault(key, {"submission": deepcopy(submission), "receipt": None,
                                                      "capture_metadata": deepcopy(capture_metadata)}))

    def complete(self, attempt_id, receipt):
        self.records[str(attempt_id)]["receipt"] = deepcopy(receipt)


class Channel:
    def __init__(self, trace):
        self.trace, self.acks, self.rejects = trace, [], []
        self.published, self.publish_confirmed = [], False

    def basic_publish(self, **kwargs):
        self.published.append(kwargs)
        return self.publish_confirmed

    def basic_ack(self, delivery_tag):
        self.trace.append("ack")
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue=False, **kwargs):
        self.rejects.append((delivery_tag, requeue))

    def basic_reject(self, delivery_tag, requeue=False):
        self.rejects.append((delivery_tag, requeue))


def deliver(runtime, event, channel, *, tag=1):
    runtime.consumer.on_message(channel, SimpleNamespace(delivery_tag=tag),
                                SimpleNamespace(headers={}), json.dumps(event).encode())


class AsgiServices:
    """Sync HTTP bridge exercising actual authenticated GM and DE handlers."""

    def __init__(self, gm, de):
        self.apps = {"gm": gm, "de": de}
        self.requests, self.submissions, self.receipts = [], [], []
        self.lose_next_de_reply = False

    def handle(self, request):
        self.requests.append((request.method, str(request.url), dict(request.headers)))
        app = self.apps[request.url.host]
        is_submission = request.method == "POST" and request.url.path == "/internal/v1/extractions"
        if is_submission:
            self.submissions.append(json.loads(request.content))

        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url=f"http://{request.url.host}") as client:
                return await client.request(request.method, request.url.raw_path.decode(),
                                            headers=request.headers, content=request.content)

        response = asyncio.run(send())
        if is_submission:
            self.receipts.append((response.status_code, response.json()))
            if self.lose_next_de_reply:
                self.lose_next_de_reply = False
                raise httpx.ReadTimeout("Recorded reply lost after DE commit", request=request)
        return httpx.Response(response.status_code, headers=response.headers,
                              content=response.content, request=request)
