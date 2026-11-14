CREATE TABLE sources (
    source_id uuid PRIMARY KEY,
    source_url text NOT NULL,
    canonical_url text NOT NULL UNIQUE,
    hostname text NOT NULL,
    source_type text NOT NULL DEFAULT 'product',
    source_fingerprint char(64) NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_attempt_at timestamptz
);
CREATE TABLE goal_source_links (
    goal_id uuid NOT NULL,
    goal_version integer NOT NULL CHECK (goal_version > 0),
    source_id uuid NOT NULL REFERENCES sources(source_id),
    state text NOT NULL CHECK (state IN ('active','inactive')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (goal_id,goal_version,source_id)
);
CREATE INDEX goal_source_active ON goal_source_links(source_id) WHERE state='active';
