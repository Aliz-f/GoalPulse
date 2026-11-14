CREATE TABLE monitors (
    monitor_id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES sources(source_id),
    policy_fingerprint char(64) NOT NULL,
    provider_monitor_id text UNIQUE,
    baseline_run_id text,
    secret_ref text,
    state text NOT NULL CHECK (state IN ('creating','active','paused','uncertain')),
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id,policy_fingerprint)
);
CREATE TABLE monitor_runs (
    provider_run_id text PRIMARY KEY,
    monitor_id uuid NOT NULL REFERENCES monitors(monitor_id),
    status text NOT NULL,
    baseline_created boolean NOT NULL DEFAULT false,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
