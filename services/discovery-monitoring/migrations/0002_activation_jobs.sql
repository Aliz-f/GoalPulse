CREATE TABLE inbox (
    event_id uuid PRIMARY KEY,
    payload_hash char(64) NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE discovery_jobs (
    job_id uuid PRIMARY KEY,
    goal_id uuid NOT NULL,
    goal_version integer NOT NULL CHECK (goal_version > 0),
    correlation_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    deadline_at timestamptz NOT NULL,
    next_attempt_at timestamptz NOT NULL,
    last_result text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (goal_id,goal_version)
);
CREATE INDEX discovery_jobs_pending ON discovery_jobs(next_attempt_at) WHERE status='pending';
