"""Activation and discovery business logic using explicit injected mock ports.

No unapproved service HTTP path or ExtractionSubmission wire model is used.
The GoalReader/EvidenceSink adapters remain the ICP-01 integration boundary.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4
import json

from jsonschema import Draft202012Validator, FormatChecker

from .context_provider import Provider, ProviderError, ScrapeResult
from .registry import Source, canonical_url


@dataclass(frozen=True)
class Runtime:
    goal_id: UUID
    current_version: int
    lifecycle: str


class GoalReader(Protocol):
    def runtime(self, goal_id: UUID) -> Runtime: ...
    def version(self, goal_id: UUID, version: int) -> dict: ...


class EvidenceSink(Protocol):
    def submit(self, source: Source, result: ScrapeResult, *, attempt_id: UUID,
               correlation_id: UUID) -> None:
        """Persist an extraction/failure through a future approved adapter."""
        ...


class ActivationInbox:
    def __init__(self, store, *, schema_directory: Path) -> None:
        self.store = store
        schema = json.loads((schema_directory / "domain-event.v1.schema.json").read_text(encoding="utf-8"))
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def accept(self, event: dict) -> bool:
        self.validator.validate(event)
        if event["event_type"] != "goal.activated.v1" or event["aggregate_id"] != event["payload"]["goal_id"]:
            raise ValueError("Not a consistent goal activation")
        # Adapter ACKs only after this durable inbox + job insertion returns.
        return self.store.accept(event)


class DiscoveryWorker:
    def __init__(self, goals: GoalReader, provider: Provider, registry,
                 evidence: EvidenceSink, *, schema_directory: Path, max_sources: int = 5) -> None:
        if type(max_sources) is not int or not 1 <= max_sources <= 5:
            raise ValueError("Source budget must be between 1 and 5")
        self.goals, self.provider, self.registry, self.evidence = goals, provider, registry, evidence
        self.max_sources = max_sources
        schema = json.loads((schema_directory / "goal-spec.v1.schema.json").read_text(encoding="utf-8"))
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def current(self, goal_id: UUID, version: int) -> bool:
        state = self.goals.runtime(goal_id)
        return (state.goal_id == goal_id and state.current_version == version and state.lifecycle == "active")

    def run(self, goal_id: UUID, version: int, correlation_id: UUID) -> str:
        if not self.current(goal_id, version):
            return "obsolete"
        goal = self.goals.version(goal_id, version)
        self.validator.validate(goal)
        if goal["goal_id"] != str(goal_id) or goal["version"] != version:
            raise ValueError("Immutable goal version mismatch")
        criteria = goal["criteria"]
        if criteria["delivery_country"] != "FI":
            return "unsupported_destination"
        queries = ([criteria["product"]["query"]] if goal["goal_type"] == "product_purchase"
                   else [item["name"] for item in criteria["items"][:self.max_sources]])
        seen, successful = set(), 0
        for query in queries:
            results = self.provider.search(query, limit=self.max_sources).get("results", [])
            for item in results[:self.max_sources]:
                if len(seen) >= self.max_sources:
                    break
                if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                    continue
                try:
                    url = canonical_url(item["url"], self.registry.resolver)
                except ProviderError:
                    continue
                from urllib.parse import urlsplit
                host = urlsplit(url).hostname
                def matches(domain):
                    return host == domain.lower() or host.endswith("." + domain.lower())
                if (any(matches(d) for d in criteria.get("blocked_seller_domains", []))
                        or (criteria.get("allowed_seller_domains")
                            and not any(matches(d) for d in criteria["allowed_seller_domains"]))):
                    continue
                if url in seen:
                    continue
                if not self.current(goal_id, version):
                    return "obsolete"
                seen.add(url)
                source = self.registry.register(url)
                self.registry.subscribe(goal_id, version, source.source_id)
                result = self.provider.scrape(source.canonical_url)
                if not self.current(goal_id, version):
                    self.registry.reconcile(goal_id, current_version=version, lifecycle="paused")
                    return "obsolete"
                self.evidence.submit(source, result, attempt_id=uuid4(), correlation_id=correlation_id)
                successful += result.product.success
        return "extracted" if successful else "no_evidence"
