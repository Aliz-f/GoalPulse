-- Provider event ID (X-Context-Id) is stable across delivery retries.
-- Delivery attempt IDs are deliberately NOT the deduplication key.
CREATE TABLE webhook_inbox (
    provider_event_id text PRIMARY KEY,
    monitor_id uuid NOT NULL REFERENCES monitors(monitor_id),
    provider_run_id text NOT NULL,
    payload_hash char(64) NOT NULL,
    received_at timestamptz NOT NULL
);
CREATE TABLE source_check_jobs (
    job_id uuid PRIMARY KEY,
    monitor_id uuid NOT NULL REFERENCES monitors(monitor_id),
    provider_run_id text NOT NULL,
    correlation_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL,
    deadline_at timestamptz NOT NULL,
    last_result text,
    UNIQUE (monitor_id,provider_run_id)
);
CREATE INDEX source_check_jobs_pending ON source_check_jobs(next_attempt_at) WHERE status='pending';
