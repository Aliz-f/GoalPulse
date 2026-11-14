-- Apply only to goal_evaluation, after migrations 001..003.
BEGIN;
CREATE TABLE evaluation_processing (
    event_id uuid PRIMARY KEY,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    state text NOT NULL CHECK (state IN ('processing','retry','succeeded','dead_letter')),
    lease_token uuid,
    lease_until timestamptz,
    next_retry_at timestamptz NOT NULL,
    last_error text,
    updated_at timestamptz NOT NULL,
    CHECK ((state='processing' AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
        OR (state<>'processing' AND lease_token IS NULL AND lease_until IS NULL))
);
CREATE INDEX evaluation_processing_retry ON evaluation_processing(next_retry_at) WHERE state='retry';
CREATE TABLE evaluation_rejections (
    rejection_id uuid PRIMARY KEY,
    event_id uuid UNIQUE,
    reason text NOT NULL CHECK (reason='invalid_event'),
    rejected_at timestamptz NOT NULL
);
COMMIT;
