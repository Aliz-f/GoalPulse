"""Use cases behind the public and internal Goal Intelligence endpoints.

Framework-free so it can be tested without FastAPI. Each user turn:
1. reads the current revision (ownership-checked),
2. asks the extractor for an untrusted patch (outside any DB transaction),
3. compiles it deterministically,
4. appends revision N+1 only if the session is still at N (else 409).
A failed extraction changes nothing: the user resends the same message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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


class ModelUnavailable(ServiceError):
    status, code = 503, "assistant_unavailable"


@dataclass
class Limits:
    max_message_chars: int = 2000
    max_user_turns: int = 20


def to_goal_draft(rec: DraftRecord) -> GoalDraftOut:
    ready = rec.state == "ready_for_confirmation"
    return GoalDraftOut(
        session_id=rec.session_id,
        owner_user_id=rec.owner_user_id,
        revision=rec.revision,
        state=rec.state,
        candidate_goal=rec.candidate_goal,
        missing_fields=[] if ready else list(rec.missing_fields),
        next_question=None if ready else rec.next_question,
        ready_for_confirmation=ready,
    )


class GoalIntelligenceService:
    def __init__(self, repo: SqlDraftRepository, extractor: ResilientExtractor, limits: Limits | None = None):
        self.repo = repo
        self.extractor = extractor
        self.limits = limits or Limits()

    # ------------------------------------------------------------------ helpers

    def _check_text(self, text: str, minimum: int) -> str:
        if not isinstance(text, str):
            raise InvalidInput("message must be text")
        text = text.strip()
        if len(text) < minimum:
            raise InvalidInput(f"message must be at least {minimum} characters")
        if len(text) > self.limits.max_message_chars:
            raise InvalidInput(f"message must be at most {self.limits.max_message_chars} characters")
        return text

    def _extract(self, text: str, state: WorkingState, session_id: str | None, revision: int | None) -> ExtractionPatch:
        request = ExtractionRequest(
            message=text,
            goal_type=state.goal_type,
            asked_fields=state.asked_fields,
            last_question=state.last_question,
            item_names=[i.name for i in state.items],
        )
        try:
            return self.extractor.extract(request)
        except InputTooLarge as exc:
            raise InvalidInput(str(exc)) from exc
        except ExtractorError as exc:
            raise ModelUnavailable(
                "The assistant couldn't process that message right now. Nothing was changed; please send it again."
            ) from exc
        finally:
            attempts = [Attempt(a.model_id, a.error_code, a.latency_ms) for a in self.extractor.attempts]
            try:
                self.repo.record_attempts(session_id, revision, attempts)
            except Exception:  # noqa: BLE001 - metrics must never break a user turn
                log.warning("could not record llm attempt metadata")

    @staticmethod
    def _revision_data(state: WorkingState, patch: ExtractionPatch) -> tuple[RevisionData, str | None]:
        result = compile_turn(state, patch)
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
        patch = self._extract(text, WorkingState(), None, None)
        data, question = self._revision_data(WorkingState(), patch)
        rec = self.repo.create_session(
            owner_user_id, data, [Message("user", text), Message("assistant", self._assistant_reply(question))]
        )
        return to_goal_draft(rec)

    def post_message(self, owner_user_id: str, session_id: str, message: str, expected_revision: int) -> GoalDraftOut:
        text = self._check_text(message, 1)
        rec = self._get_owned(session_id, owner_user_id)
        if rec.state not in ("collecting", "ready_for_confirmation"):
            raise Conflict(f"session is {rec.state}", rec.revision)
        if rec.expires_at <= self.repo.clock():
            raise Conflict("this conversation expired; please start a new goal", rec.revision)
        if expected_revision != rec.revision:
            raise Conflict("stale revision; reload the draft", rec.revision)
        if self.repo.count_user_messages(session_id) >= self.limits.max_user_turns:
            raise Conflict("this conversation reached its turn limit; please start a new goal", rec.revision)

        state = WorkingState.model_validate(rec.working_state)
        patch = self._extract(text, state, session_id, rec.revision)
        data, question = self._revision_data(state, patch)
        try:
            new = self.repo.append_revision(
                session_id, owner_user_id, expected_revision, data,
                [Message("user", text), Message("assistant", self._assistant_reply(question))],
            )
        except SessionClosed as exc:
            raise Conflict("session no longer accepts messages") from exc
        except Exception as exc:
            _translate(exc)
            raise
        return to_goal_draft(new)

    def get_session(self, owner_user_id: str, session_id: str) -> GoalDraftOut:
        return to_goal_draft(self._get_owned(session_id, owner_user_id))

    def _get_owned(self, session_id: str, owner_user_id: str) -> DraftRecord:
        try:
            return self.repo.get_current(session_id, owner_user_id)
        except Exception as exc:
            _translate(exc)
            raise

    # ------------------------------------------------------------------ internal API (GM only)

    def get_candidate(self, session_id: str, expected_revision: int) -> GoalDraftOut:
        try:
            rec = self.repo.get_current(session_id, None)
        except Exception as exc:
            _translate(exc)
            raise
        if rec.revision != expected_revision:
            raise Conflict("stale revision", rec.revision)
        if rec.state != "ready_for_confirmation":
            raise Conflict(f"draft is {rec.state}, not ready for confirmation", rec.revision)
        return to_goal_draft(rec)

    def mark_confirmed(self, session_id: str, goal_id: str, expected_revision: int) -> None:
        try:
            self.repo.mark_confirmed(session_id, expected_revision, goal_id)
        except Exception as exc:
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
