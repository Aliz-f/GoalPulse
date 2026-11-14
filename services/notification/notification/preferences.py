"""Local owner-scoped preferences. Email transport/opt-in setup is deferred."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, ContextManager, Protocol

from .reads import AuthenticationRequired, VerifiedPrincipal


@dataclass(frozen=True)
class Preferences:
    in_app_enabled: bool = True
    email_enabled: bool = False

    @property
    def email_delivery_available(self) -> bool:
        return False  # a stored preference alone never enables an email sender


class PreferenceTransaction(Protocol):
    def get_preferences(self, user_id: str) -> Preferences: ...
    def set_in_app_preference(self, user_id: str, enabled: bool, now: datetime) -> None: ...


class PreferenceStore(Protocol):
    def transaction(self) -> ContextManager[PreferenceTransaction]: ...


class PreferenceAccess:
    def __init__(self, store: PreferenceStore, identity: Callable[[], VerifiedPrincipal | None],
                 clock: Callable[[], datetime]):
        self.store, self.identity, self.clock = store, identity, clock

    def _subject(self) -> str:
        principal = self.identity()
        if not isinstance(principal, VerifiedPrincipal):
            raise AuthenticationRequired()
        return principal.subject

    def get(self) -> Preferences:
        subject = self._subject()
        with self.store.transaction() as transaction:
            return transaction.get_preferences(subject)

    def set_in_app(self, enabled: bool) -> None:
        subject = self._subject()
        if type(enabled) is not bool:
            raise ValueError('In-app preference must be boolean')
        now = self.clock()
        if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
            raise ValueError('Clock must be timezone-aware UTC')
        with self.store.transaction() as transaction:
            transaction.set_in_app_preference(subject, enabled, now)
