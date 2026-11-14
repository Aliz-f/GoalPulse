-- Goal Intelligence owned schema (database: goal_intelligence). PostgreSQL 15+.
-- D2-GI-01: sessions, transcript, immutable draft revisions, LLM attempt metadata.
-- No other service reads these tables; GM uses the internal HTTP candidate API.

BEGIN;

CREATE TABLE IF NOT EXISTS sessions (
    session_id             uuid PRIMARY KEY,
    owner_user_id          uuid        NOT NULL,
    state                  text        NOT NULL CHECK (state IN ('collecting','ready_for_confirmation','confirmed','abandoned')),
    current_revision       integer     NOT NULL CHECK (current_revision >= 1),
    ready_for_confirmation boolean     NOT NULL DEFAULT false,
    confirmed_goal_id      uuid        NULL,
    created_at             timestamptz NOT NULL,
    updated_at             timestamptz NOT NULL,
    expires_at             timestamptz NOT NULL,
    CHECK (state <> 'ready_for_confirmation' OR ready_for_confirmation),
    CHECK ((state = 'confirmed') = (confirmed_goal_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS sessions_owner_idx ON sessions (owner_user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    message_id  uuid PRIMARY KEY,
    session_id  uuid        NOT NULL REFERENCES sessions (session_id),
    revision    integer     NOT NULL,
    role        text        NOT NULL CHECK (role IN ('user','assistant','system')),
    content     text        NOT NULL,
    token_count integer     NULL,
    created_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session_idx ON messages (session_id, created_at);

CREATE TABLE IF NOT EXISTS draft_revisions (
    session_id     uuid        NOT NULL REFERENCES sessions (session_id),
    revision       integer     NOT NULL CHECK (revision >= 1),
    state          text        NOT NULL,
    candidate_goal jsonb       NOT NULL,
    missing_fields jsonb       NOT NULL,
    next_question  text        NULL,
    ready          boolean     NOT NULL,
    working_state  jsonb       NOT NULL,
    created_at     timestamptz NOT NULL,
    PRIMARY KEY (session_id, revision)
);

-- Revisions are immutable snapshots: the user confirms exactly what they reviewed.
CREATE OR REPLACE FUNCTION draft_revisions_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'draft_revisions are immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS draft_revisions_no_update ON draft_revisions;
CREATE TRIGGER draft_revisions_no_update
    BEFORE UPDATE OR DELETE ON draft_revisions
    FOR EACH ROW EXECUTE FUNCTION draft_revisions_immutable();

-- Metadata only: never the prompt, the response or an API key.
CREATE TABLE IF NOT EXISTS llm_attempts (
    id          uuid PRIMARY KEY,
    session_id  uuid        NULL,
    revision    integer     NULL,
    model_id    text        NOT NULL,
    error_code  text        NULL,
    latency_ms  integer     NOT NULL,
    created_at  timestamptz NOT NULL
);

COMMIT;
