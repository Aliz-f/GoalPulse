"""Source/subscription registry behind the accepted authenticated read API."""

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .context_provider import public_addresses, public_https_url
from .persistence import Database


def canonical_url(url: str, resolver=public_addresses) -> str:
    parsed = urlsplit(public_https_url(url, resolver))
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in ("gclid", "fbclid")]
    # Product/variant selectors are retained. Query ordering may be significant
    # to a merchant, so never sort or merge repeated parameters.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


@dataclass(frozen=True)
class Source:
    source_id: UUID
    source_url: str
    canonical_url: str
    hostname: str


def source_row(row) -> Source:
    return Source(UUID(str(row[0])), row[1], row[2], row[3])


class SourceRegistry:
    def __init__(self, database: Database, *, resolver=public_addresses) -> None:
        self.database, self.resolver = database, resolver

    def register(self, url: str) -> Source:
        safe = public_https_url(url, self.resolver)
        canonical = canonical_url(safe, self.resolver)
        with self.database.transaction() as cursor:
            cursor.execute("""INSERT INTO sources
                (source_id,source_url,canonical_url,hostname,source_fingerprint)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (canonical_url) DO NOTHING
                RETURNING source_id,source_url,canonical_url,hostname""",
                (str(uuid4()), safe, canonical, urlsplit(canonical).hostname,
                 sha256(canonical.encode()).hexdigest()))
            row = cursor.fetchone()
            if row is None:
                cursor.execute("SELECT source_id,source_url,canonical_url,hostname FROM sources WHERE canonical_url=%s", (canonical,))
                row = cursor.fetchone()
            return source_row(row)

    def get(self, source_id: UUID) -> Source | None:
        with self.database.transaction() as cursor:
            cursor.execute("SELECT source_id,source_url,canonical_url,hostname FROM sources WHERE source_id=%s", (str(source_id),))
            row = cursor.fetchone()
            return source_row(row) if row else None

    def subscribe(self, goal_id: UUID, goal_version: int, source_id: UUID) -> None:
        if type(goal_version) is not int or goal_version < 1:
            raise ValueError("Invalid goal version")
        with self.database.transaction() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"sources:{goal_id}",))
            cursor.execute("SELECT source_id FROM goal_source_links WHERE goal_id=%s AND goal_version=%s AND state='active' ORDER BY updated_at,source_id",
                           (str(goal_id), goal_version))
            active = [str(row[0]) for row in cursor.fetchall()]
            if str(source_id) not in active and len(active) >= 3:
                # Replace older candidates as discovery changes; never keep more
                # than three active URLs for this goal/version even mid-scan.
                for old in active[:len(active) - 2]:
                    cursor.execute("UPDATE goal_source_links SET state='inactive' WHERE goal_id=%s AND goal_version=%s AND source_id=%s",
                                   (str(goal_id), goal_version, old))
            cursor.execute("""INSERT INTO goal_source_links (goal_id,goal_version,source_id,state)
                VALUES (%s,%s,%s,'active') ON CONFLICT (goal_id,goal_version,source_id)
                DO UPDATE SET state='active',updated_at=now()""", (str(goal_id), goal_version, str(source_id)))

    def subscriptions(self, source_id: UUID) -> list[tuple[UUID, int]]:
        with self.database.transaction() as cursor:
            cursor.execute("""SELECT goal_id,goal_version FROM goal_source_links
                WHERE source_id=%s AND state='active' ORDER BY goal_id,goal_version""", (str(source_id),))
            return [(UUID(str(row[0])), row[1]) for row in cursor.fetchall()]

    def sources_for_goal(self, goal_id: UUID, goal_version: int) -> list[UUID] | None:
        """Return one snapshot of the complete active set for an exact activation.

        ``None`` means DM never accepted that goal version.  An empty list means
        the activation is known but currently has no active links.  The single
        statement prevents a concurrent reconciliation from producing a mixed
        known/link snapshot, and corrupted over-bound state fails closed.
        """
        if type(goal_version) is not int or goal_version < 1:
            raise ValueError("Invalid goal version")
        with self.database.transaction() as cursor:
            cursor.execute("""WITH known AS (
                    SELECT EXISTS(
                        SELECT 1 FROM discovery_jobs
                        WHERE goal_id=%s AND goal_version=%s
                    ) AS present
                ), active AS (
                    SELECT source_id FROM goal_source_links
                    WHERE goal_id=%s AND goal_version=%s AND state='active'
                )
                SELECT known.present,active.source_id FROM known
                LEFT JOIN active ON true ORDER BY active.source_id""",
                (str(goal_id), goal_version, str(goal_id), goal_version))
            rows = cursor.fetchall()
        if not rows or rows[0][0] is not True:
            return None
        sources = [UUID(str(row[1])) for row in rows if row[1] is not None]
        if len(sources) > 3 or len(set(sources)) != len(sources):
            raise RuntimeError("Goal source state violates the approved bound")
        return sources

    def reconcile(self, goal_id: UUID, *, current_version: int, lifecycle: str) -> None:
        if lifecycle not in ("active", "paused", "cancelled", "completed") or current_version < 1:
            raise ValueError("Invalid runtime state")
        with self.database.transaction() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"sources:{goal_id}",))
            cursor.execute("""UPDATE goal_source_links SET
                state=CASE WHEN goal_version=%s AND %s='active' AND source_id IN
                    (SELECT source_id FROM goal_source_links WHERE goal_id=%s AND goal_version=%s
                     ORDER BY updated_at DESC,source_id LIMIT 3)
                THEN 'active' ELSE 'inactive' END WHERE goal_id=%s""",
                (current_version, lifecycle, str(goal_id), current_version, str(goal_id)))

    def suspend(self, goal_id: UUID) -> None:
        """Stop owned monitoring links for a quota/policy block, without changing GM."""
        with self.database.transaction() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"sources:{goal_id}",))
            cursor.execute("UPDATE goal_source_links SET state='inactive' WHERE goal_id=%s", (str(goal_id),))

    def linked_goals(self) -> list[UUID]:
        with self.database.transaction() as cursor:
            cursor.execute("SELECT DISTINCT goal_id FROM goal_source_links ORDER BY goal_id")
            return [UUID(str(row[0])) for row in cursor.fetchall()]

    def goals_for_source(self, source_id: UUID) -> list[UUID]:
        with self.database.transaction() as cursor:
            cursor.execute("SELECT DISTINCT goal_id FROM goal_source_links WHERE source_id=%s ORDER BY goal_id",
                           (str(source_id),))
            return [UUID(str(row[0])) for row in cursor.fetchall()]
