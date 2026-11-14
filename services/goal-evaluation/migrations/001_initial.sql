-- Apply only to the Goal Evaluation owned PostgreSQL database.
BEGIN;
CREATE TABLE evaluations (
    evaluation_id uuid PRIMARY KEY,
    goal_id uuid NOT NULL,
    goal_version integer NOT NULL CHECK (goal_version > 0),
    evaluated_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('satisfied', 'unsatisfied', 'unknown')),
    payload jsonb NOT NULL,
    evaluator_version text NOT NULL,
    fingerprint text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evaluations_goal_version ON evaluations (goal_id, goal_version, evaluated_at);
CREATE TABLE condition_results (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evaluation_id uuid NOT NULL REFERENCES evaluations(evaluation_id),
    code text NOT NULL,
    status text NOT NULL CHECK (status IN ('satisfied', 'unsatisfied', 'unknown')),
    reason text NOT NULL,
    observation_ids jsonb NOT NULL CHECK (jsonb_typeof(observation_ids) = 'array'),
    UNIQUE (evaluation_id, code)
);
CREATE TABLE offer_fingerprints (
    goal_id uuid NOT NULL,
    goal_version integer NOT NULL CHECK (goal_version > 0),
    fingerprint text NOT NULL,
    last_verified_at timestamptz NOT NULL,
    notification_emitted boolean NOT NULL DEFAULT false,
    PRIMARY KEY (goal_id, goal_version, fingerprint)
);
CREATE TABLE inbox (
    event_id uuid PRIMARY KEY,
    processed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE outbox (
    event_id uuid PRIMARY KEY,
    event_json jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'published', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_retry_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    last_error text
);
CREATE INDEX outbox_pending ON outbox (next_retry_at) WHERE status = 'pending';
COMMIT;
