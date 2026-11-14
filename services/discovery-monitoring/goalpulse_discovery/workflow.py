"""Activation and bounded discovery through authenticated injectable ports."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid5, NAMESPACE_URL
from urllib.parse import urlsplit

from .context_provider import Provider, ProviderError, ScrapeResult
from .registry import Source, canonical_url
from .validation import validator


@dataclass(frozen=True)
class Runtime:
    goal_id: UUID
    current_version: int
    lifecycle: str
    owner_user_id: UUID | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.goal_id, UUID) or type(self.current_version) is not int
                or self.current_version < 1 or self.lifecycle not in ("active", "paused", "cancelled", "completed")
                or (self.owner_user_id is not None and not isinstance(self.owner_user_id, UUID))):
            raise ValueError("Invalid authenticated goal runtime")


class GoalReader(Protocol):
    def runtime(self, goal_id: UUID) -> Runtime: ...
    def version(self, goal_id: UUID, version: int) -> dict: ...


class EvidenceSink(Protocol):
    def submit(self, source: Source, result: ScrapeResult, *, attempt_id: UUID,
               correlation_id: UUID) -> None:
        """Persist an extraction/failure through the accepted internal API."""
        ...


class ActivationInbox:
    def __init__(self, store, *, schema_directory: Path) -> None:
        self.store = store
        self.validator = validator(schema_directory / "domain-event.v1.schema.json")

    def accept(self, event: dict) -> bool:
        self.validator.validate(event)
        if event["event_type"] != "goal.activated.v1" or event["aggregate_id"] != event["payload"]["goal_id"]:
            raise ValueError("Not a consistent goal activation")
        # Adapter ACKs only after this durable inbox + job insertion returns.
        return self.store.accept(event)


class DiscoveryWorker:
    def __init__(self, goals: GoalReader, provider: Provider, registry,
                 evidence: EvidenceSink, *, schema_directory: Path, max_sources: int = 5,
                 plans=None, resume_evidence=None, scan_id: UUID | None = None) -> None:
        if type(max_sources) is not int or not 1 <= max_sources <= 5:
            raise ValueError("Source budget must be between 1 and 5")
        self.goals, self.provider, self.registry, self.evidence = goals, provider, registry, evidence
        self.max_sources = max_sources
        self.plans, self.resume_evidence = plans, resume_evidence
        self.scan_id = scan_id
        self.validator = validator(schema_directory / "goal-spec.v1.schema.json")

    def current(self, goal_id: UUID, version: int) -> bool:
        state = self.goals.runtime(goal_id)
        return (state.goal_id == goal_id and state.current_version == version and state.lifecycle == "active")

    def reconcile_current(self, goal_id: UUID, version: int) -> bool:
        """Reconcile only from authoritative runtime; stale jobs never invent state."""
        state = self.goals.runtime(goal_id)
        if state.goal_id != goal_id:
            raise ValueError("Runtime identity mismatch")
        if state.current_version == version and state.lifecycle == "active":
            return True
        self.registry.reconcile(goal_id, current_version=state.current_version,
                                lifecycle=state.lifecycle)
        return False

    @staticmethod
    def _allowed(url: str, criteria: dict) -> bool:
        host = urlsplit(url).hostname
        def matches(domain):
            return host == domain.lower() or host.endswith("." + domain.lower())
        return not (any(matches(d) for d in criteria.get("blocked_seller_domains", []))
                    or (criteria.get("allowed_seller_domains")
                        and not any(matches(d) for d in criteria["allowed_seller_domains"])))

    def _discover(self, goal: dict) -> list[str]:
        criteria = goal["criteria"]
        queries = ([criteria["product"]["query"]] if goal["goal_type"] == "product_purchase"
                   else [item["name"] for item in criteria["items"][:self.max_sources]])
        groups = [iter(self.provider.search(query, limit=self.max_sources).get("results", [])[:self.max_sources])
                  for query in queries]
        urls = []
        while groups and len(urls) < self.max_sources:
            for group in list(groups):
                try:
                    item = next(group)
                except StopIteration:
                    groups.remove(group)
                    continue
                if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                    continue
                try:
                    url = canonical_url(item["url"], self.registry.resolver)
                except ProviderError:
                    continue
                if self._allowed(url, criteria) and url not in urls:
                    urls.append(url)
                if len(urls) >= self.max_sources:
                    break
        return urls

    def run(self, goal_id: UUID, version: int, correlation_id: UUID) -> str:
        if not self.reconcile_current(goal_id, version):
            return "obsolete"
        goal = self.goals.version(goal_id, version)
        self.validator.validate(goal)
        if goal["goal_id"] != str(goal_id) or goal["version"] != version:
            raise ValueError("Immutable goal version mismatch")
        criteria = goal["criteria"]
        if criteria["delivery_country"] != "FI":
            return "unsupported_destination"
        urls = self.plans.load_plan(goal_id, version) if self.plans is not None else None
        if urls is None:
            urls = self._discover(goal)
            if self.plans is not None:
                urls = self.plans.save_plan(goal_id, version, urls)
        successful = 0
        for url in urls[:self.max_sources]:
            # Persisted plans are revalidated on replay, including current DNS.
            url = canonical_url(url, self.registry.resolver)
            if not self._allowed(url, criteria):
                continue
            if not self.reconcile_current(goal_id, version):
                return "obsolete"
            identity = f"goalpulse:initial:{goal_id}:{version}:{url}" if self.scan_id is None else f"goalpulse:scan:{self.scan_id}:{url}"
            attempt_id = uuid5(NAMESPACE_URL, identity)
            if self.resume_evidence is not None:
                resumed = self.resume_evidence(attempt_id)
                if resumed is not None:
                    successful += resumed["accepted"] is True
                    continue
            source = self.registry.register(url)
            result = self.provider.scrape(source.canonical_url)
            final_url = canonical_url(result.source_url, self.registry.resolver)
            if (urlsplit(final_url).hostname != source.hostname or not self._allowed(final_url, criteria)):
                raise ProviderError("UNSAFE_REDIRECT")
            if final_url != source.canonical_url:
                source = self.registry.register(final_url)
            if not self.reconcile_current(goal_id, version):
                return "obsolete"
            self.registry.subscribe(goal_id, version, source.source_id)
            receipt = self.evidence.submit(source, result, attempt_id=attempt_id, correlation_id=correlation_id)
            successful += receipt["accepted"] is True if isinstance(receipt, dict) else result.product.success
        return "extracted" if successful else "no_evidence"
