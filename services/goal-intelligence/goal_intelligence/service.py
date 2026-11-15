"""Use cases behind the public and internal Goal Intelligence endpoints.

Framework-free so it can be tested without FastAPI. Each user turn:
1. reads the current revision (ownership-checked),
2. asks the extractor for an untrusted patch (outside any DB transaction),
3. compiles it deterministically,
4. appends revision N+1 only if the session is still at N (else 409).
Provider failure (timeout, rate limit, malformed output after bounded retries) is visible
and fails closed: the user's message is still recorded as revision N+1, but nothing from
the failed call is applied, so that revision's candidate and missing fields equal N's,
it can never be ready, and next_question starts with RETRY_NOTICE. There is no fallback
to another provider and no undocumented 503 (the frozen API lists none).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from .domain.compiler import WorkingState, compile_turn
from .domain.extraction import ExtractionPatch, ExtractionRequest
from .llm.base import ExtractorError, InputTooLarge, ResilientExtractor
from .persistence.repository import (
    Attempt,
    DraftRecord,
    Message,
    RevisionData,
    SessionClosed,
    SqlDraftRepository,
)

log = logging.getLogger("goal_intelligence.service")


class GoalDraftOut(BaseModel):
    """Exact GoalDraft.v1 response shape (additionalProperties: false)."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    owner_user_id: str
    revision: int
    state: str
    candidate_goal: dict[str, Any]
    missing_fields: list[str]
    next_question: str | None
    ready_for_confirmation: bool


class ServiceError(Exception):
    status = 500
    code = "internal_error"


class NotFound(ServiceError):
    status, code = 404, "session_not_found"


class Conflict(ServiceError):
    status, code = 409, "conflict"

    def __init__(self, message: str, current_revision: int | None = None):
        super().__init__(message)
        self.current_revision = current_revision


class InvalidInput(ServiceError):
    status, code = 422, "invalid_input"


@dataclass
class Limits:
    max_message_chars: int = 2000
    max_user_turns: int = 20


OPEN_STATES = ("collecting", "ready_for_confirmation")
RETRY_NOTICE = (
    "The AI assistant is unavailable right now, so nothing in your goal was changed. "
    "Please send your message again."
)
SUMMARY_APPROVAL_MESSAGE = "Yes, set it up exactly like this without changes."
_SUMMARY_APPROVAL_REPLIES = frozenset(
    {
        "approve",
        "approved",
        "confirm",
        "deal",
        "looks good",
        "looks good to me",
        "set it up",
        "yes",
        "yes please",
        "yes set it up",
        "yes, set it up exactly like this without changes",
        "yes set it up exactly like this without changes",
    }
)
EXPIRED_QUESTION = "This conversation expired. Please start a new goal."


def to_goal_draft(rec: DraftRecord, now: datetime | None = None) -> GoalDraftOut:
    """Exact GoalDraft.v1. An open session past its expiry is reported as ``abandoned``."""
    state = rec.state
    question = rec.next_question
    if state in OPEN_STATES and now is not None and rec.expires_at <= now:
        state, question = "abandoned", EXPIRED_QUESTION
    ready = state == "ready_for_confirmation"
    return GoalDraftOut(
        session_id=rec.session_id,
        owner_user_id=rec.owner_user_id,
        revision=rec.revision,
        state=state,
        candidate_goal=rec.candidate_goal,
        missing_fields=[] if ready else list(rec.missing_fields),
        next_question=None if ready else question,
        ready_for_confirmation=ready,
    )


class GoalIntelligenceService:
    def __init__(self, repo: SqlDraftRepository, extractor: ResilientExtractor, limits: Limits | None = None):
        self.repo = repo
        self.extractor = extractor
        self.limits = limits or Limits()

    # ------------------------------------------------------------------ helpers

    def _now(self) -> datetime:
        return self.repo.clock()

    def _check_text(self, text: str, minimum: int) -> str:
        if not isinstance(text, str):
            raise InvalidInput("message must be text")
        text = text.strip()
        if len(text) < minimum:
            raise InvalidInput(f"message must be at least {minimum} characters")
        if len(text) > self.limits.max_message_chars:
            raise InvalidInput(f"message must be at most {self.limits.max_message_chars} characters")
        return text

    def _extract(
        self, text: str, state: WorkingState, session_id: str | None, revision: int | None
    ) -> tuple[ExtractionPatch, list[str]]:
        """Returns (patch, notices). A failed model call yields an empty patch plus a
        retry notice: the frozen API has no 503, so the failure surfaces as a recoverable
        clarification and nothing the model said is applied."""
        # Consent is authoritative user input, not a semantic fact for an LLM to
        # interpret. Accept only a narrow, exact affirmative while the canonical
        # summary is the current question. Replies containing edits still go through
        # extraction, and Goal Management still requires the separate exact-revision
        # confirmation before monitoring starts.
        normalized = text.casefold().strip().rstrip(".!?").strip()
        if state.asked_fields == ["summary_approval"] and normalized in _SUMMARY_APPROVAL_REPLIES:
            return ExtractionPatch(approve_summary=True), []
        request = ExtractionRequest(
            message=text,
            goal_type=state.goal_type,
            asked_fields=state.asked_fields,
            last_question=state.last_question,
            item_names=[i.name for i in state.items],
        )
        try:
            return self.extractor.extract(request), []
        except InputTooLarge as exc:
            raise InvalidInput(str(exc)) from exc
        except ExtractorError:
            return ExtractionPatch(), [RETRY_NOTICE]
        finally:
            attempts = [Attempt(a.model_id, a.error_code, a.latency_ms) for a in self.extractor.attempts]
            try:
                self.repo.record_attempts(session_id, revision, attempts)
            except Exception:  # noqa: BLE001 - metrics must never break a user turn
                log.warning("could not record llm attempt metadata")

    @staticmethod
    def _revision_data(state: WorkingState, patch: ExtractionPatch, notices: list[str]) -> tuple[RevisionData, str | None]:
        failed = RETRY_NOTICE in notices
        result = compile_turn(state, patch, extra_notices=notices, withdraw_approval=failed)
        data = RevisionData(
            state="ready_for_confirmation" if result.ready else "collecting",
            candidate_goal=result.candidate_goal,
            missing_fields=result.missing_fields,
            next_question=result.next_question,
            ready=result.ready,
            working_state=result.state.model_dump(mode="json"),
        )
        return data, result.next_question

    @staticmethod
    def _assistant_reply(question: str | None) -> str:
        return question or "Everything is ready. Review the summary and confirm to start monitoring."

    # ------------------------------------------------------------------ public API

    def create_session(self, owner_user_id: str, initial_message: str) -> GoalDraftOut:
        text = self._check_text(initial_message, 3)
        patch, notices = self._extract(text, WorkingState(), None, None)
        data, question = self._revision_data(WorkingState(), patch, notices)
        rec = self.repo.create_session(
            owner_user_id, data, [Message("user", text), Message("assistant", self._assistant_reply(question))]
        )
        return to_goal_draft(rec, self._now())

    def post_message(self, owner_user_id: str, session_id: str, message: str, expected_revision: int) -> GoalDraftOut:
        text = self._check_text(message, 1)
        rec = self._get_owned(session_id, owner_user_id)
        if to_goal_draft(rec, self._now()).state not in OPEN_STATES:
            raise Conflict("this conversation is closed; please start a new goal", rec.revision)
        if expected_revision != rec.revision:
            raise Conflict("stale revision; reload the draft", rec.revision)
        if self.repo.count_user_messages(session_id) >= self.limits.max_user_turns:
            self.repo.abandon(session_id, owner_user_id)
            raise Conflict("this conversation reached its turn limit; please start a new goal", rec.revision)

        state = WorkingState.model_validate(rec.working_state)
        patch, notices = self._extract(text, state, session_id, rec.revision)
        data, question = self._revision_data(state, patch, notices)
        try:
            new = self.repo.append_revision(
                session_id, owner_user_id, expected_revision, data,
                [Message("user", text), Message("assistant", self._assistant_reply(question))],
            )
        except Exception as exc:
            _translate(exc)
            raise
        return to_goal_draft(new, self._now())

    def get_session(self, owner_user_id: str, session_id: str) -> GoalDraftOut:
        return to_goal_draft(self._get_owned(session_id, owner_user_id), self._now())

    def _get_owned(self, session_id: str, owner_user_id: str) -> DraftRecord:
        try:
            return self.repo.get_current(session_id, owner_user_id)
        except Exception as exc:
            _translate(exc)
            raise

    # ------------------------------------------------------------------ internal API (GM only)

    def get_candidate(self, session_id: str, expected_revision: int) -> GoalDraftOut:
        """ICP-01: 200 exact ready revision; 404 unknown session; 409 stale or not ready."""
        try:
            rec = self.repo.get_current(session_id, None)
        except Exception as exc:
            _translate(exc)
            raise
        draft = to_goal_draft(rec, self._now())
        if draft.revision != expected_revision:
            raise Conflict("stale revision", rec.revision)
        if draft.state != "ready_for_confirmation":
            raise Conflict(f"draft is {draft.state}, not ready for confirmation", rec.revision)
        return draft

    def mark_confirmed(self, session_id: str, goal_id: str, expected_revision: int) -> None:
        """ICP-01 lists only 204 / 403 / 409, so an unknown session is a 409 mismatch."""
        try:
            self.repo.mark_confirmed(session_id, expected_revision, goal_id)
        except Exception as exc:
            from .persistence.repository import SessionNotFound

            if isinstance(exc, SessionNotFound):
                raise Conflict("no such session to mark confirmed") from exc
            _translate(exc)
            raise


def _translate(exc: Exception) -> None:
    from .persistence.repository import ConfirmConflict, SessionNotFound, StaleRevision

    if isinstance(exc, SessionNotFound):
        raise NotFound("session not found") from exc
    if isinstance(exc, StaleRevision):
        raise Conflict("stale revision; reload the draft", exc.current_revision) from exc
    if isinstance(exc, (SessionClosed, ConfirmConflict)):
        raise Conflict(str(exc)) from exc
