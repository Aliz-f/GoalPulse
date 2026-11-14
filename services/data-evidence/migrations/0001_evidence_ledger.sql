-- D3-DE-01: execute only against the service-owned data_evidence database.
-- No cross-service foreign keys, no raw page blobs, and no destructive down step.

CREATE TABLE extraction_attempts (
    attempt_id uuid PRIMARY KEY,
    source_id uuid NOT NULL,
    provider text NOT NULL CHECK (provider IN ('context_dev', 'fixture')),
    provider_request_id text,
    extraction_method text NOT NULL CHECK (extraction_method IN ('search','scrape','monitor','fixture')),
    source_url text NOT NULL,
    fetched_at timestamptz NOT NULL,
    provider_observed_at timestamptz,
    cache_age_ms bigint CHECK (cache_age_ms >= 0),
    outcome text NOT NULL CHECK (outcome IN ('accepted','rejected')),
    error_code text,
    fingerprint char(64) NOT NULL UNIQUE CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    CHECK (provider_observed_at IS NULL OR provider_observed_at <= fetched_at),
    CHECK ((outcome = 'accepted' AND error_code IS NULL)
        OR (outcome = 'rejected' AND error_code IS NOT NULL))
);
CREATE INDEX extraction_attempts_source_created ON extraction_attempts (source_id, created_at DESC);
CREATE INDEX extraction_attempts_provider_request ON extraction_attempts (provider, provider_request_id);

CREATE TABLE observations (
    observation_id uuid PRIMARY KEY,
    source_id uuid NOT NULL,
    source_url text NOT NULL,
    observed_at timestamptz NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    reliability text NOT NULL CHECK (reliability IN ('unverified','checked','disputed')),
    extraction_attempt_id uuid NOT NULL REFERENCES extraction_attempts (attempt_id),
    normalized_fingerprint char(64) NOT NULL UNIQUE CHECK (normalized_fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    CHECK (payload->>'observation_id' = observation_id::text),
    CHECK (payload->>'source_id' = source_id::text),
    CHECK (payload->>'source_url' = source_url),
    CHECK ((payload->>'observed_at')::timestamptz = observed_at),
    CHECK (payload->'evidence'->>'reliability' = reliability)
);
CREATE INDEX observations_source_observed ON observations (source_id, observed_at DESC, observation_id);

CREATE TABLE observation_provenance (
    observation_id uuid PRIMARY KEY REFERENCES observations (observation_id),
    provider_request_id text,
    page_url text NOT NULL,
    cache_age_ms bigint CHECK (cache_age_ms >= 0),
    field_sources jsonb NOT NULL CHECK (jsonb_typeof(field_sources) = 'object'),
    parser_version text NOT NULL,
    extraction_version text NOT NULL CHECK (extraction_version = 'observation.v1')
);

CREATE TABLE outbox (
    event_id uuid PRIMARY KEY,
    observation_id uuid NOT NULL UNIQUE REFERENCES observations (observation_id),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','published','dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL,
    published_at timestamptz,
    last_error_code text,
    created_at timestamptz NOT NULL,
    CHECK (payload->>'event_type' = 'evidence.updated.v1'),
    CHECK (payload->>'producer' = 'data-evidence'),
    CHECK (payload->>'event_id' = event_id::text),
    CHECK (payload->'payload'->>'observation_id' = observation_id::text),
    CHECK ((status = 'published') = (published_at IS NOT NULL))
);
CREATE INDEX outbox_pending ON outbox (next_attempt_at, created_at) WHERE status = 'pending';

CREATE TABLE inbox (
    event_id uuid PRIMARY KEY,
    processed_at timestamptz NOT NULL
);

CREATE FUNCTION de_reject_immutable_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Evidence ledger records are immutable' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER immutable_observations BEFORE UPDATE OR DELETE ON observations
    FOR EACH ROW EXECUTE FUNCTION de_reject_immutable_mutation();
CREATE TRIGGER immutable_provenance BEFORE UPDATE OR DELETE ON observation_provenance
    FOR EACH ROW EXECUTE FUNCTION de_reject_immutable_mutation();
CREATE TRIGGER immutable_attempts BEFORE UPDATE OR DELETE ON extraction_attempts
    FOR EACH ROW EXECUTE FUNCTION de_reject_immutable_mutation();
