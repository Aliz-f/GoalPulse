"""Create Goal Management-owned tables with immutable version constraints."""
from alembic import op

revision = "0001_goal_management_schema"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
    CREATE TABLE goals (
      goal_id UUID PRIMARY KEY, owner_user_id UUID NOT NULL,
      lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active','paused','completed','cancelled')),
      current_version INTEGER NOT NULL CHECK (current_version >= 1),
      confirmed_session_id UUID NOT NULL UNIQUE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX goals_owner_user_id_idx ON goals(owner_user_id);
    CREATE TABLE goal_versions (
      goal_id UUID NOT NULL REFERENCES goals(goal_id) ON DELETE RESTRICT,
      version INTEGER NOT NULL CHECK (version >= 1), spec JSONB NOT NULL,
      approved_at TIMESTAMPTZ NOT NULL, approved_draft_revision INTEGER NOT NULL CHECK (approved_draft_revision >= 1),
      PRIMARY KEY (goal_id, version)
    );
    CREATE FUNCTION prevent_goal_version_mutation() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'goal_versions are immutable';
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER goal_versions_immutable
      BEFORE UPDATE OR DELETE ON goal_versions
      FOR EACH ROW EXECUTE FUNCTION prevent_goal_version_mutation();
    CREATE TABLE goal_evaluation_projection (
      goal_id UUID PRIMARY KEY REFERENCES goals(goal_id) ON DELETE RESTRICT,
      version INTEGER NOT NULL CHECK (version >= 1), evaluation_id UUID NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('satisfied','unsatisfied','unknown')),
      last_evaluated_at TIMESTAMPTZ NOT NULL, opportunity_status TEXT NOT NULL CHECK (opportunity_status IN ('not_found','candidate','verified','unknown')),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE inbox (event_id UUID PRIMARY KEY, event_type TEXT NOT NULL, processed_at TIMESTAMPTZ NOT NULL DEFAULT now());
    CREATE TABLE outbox (
      id UUID PRIMARY KEY, event_id UUID NOT NULL UNIQUE, event_type TEXT NOT NULL,
      payload_jsonb JSONB NOT NULL, status TEXT NOT NULL CHECK (status IN ('pending','publishing','delivered','failed')),
      attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0), next_attempt_at TIMESTAMPTZ NOT NULL,
      claimed_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), delivered_at TIMESTAMPTZ
    );
    CREATE INDEX outbox_pending_idx ON outbox(status, next_attempt_at, claimed_at);
    """)

def downgrade() -> None:
    op.execute("DROP TABLE outbox; DROP TABLE inbox; DROP TABLE goal_evaluation_projection; DROP TABLE goal_versions; DROP FUNCTION prevent_goal_version_mutation; DROP TABLE goals;")
