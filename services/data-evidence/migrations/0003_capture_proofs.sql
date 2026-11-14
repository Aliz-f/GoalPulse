-- ADR-0004: immutable seller representation transported separately from facts.
CREATE TABLE evidence_capture_proofs (
    attempt_id uuid PRIMARY KEY REFERENCES extraction_attempts(attempt_id),
    source_url text NOT NULL CHECK (source_url LIKE 'https://%'),
    fetched_at timestamptz NOT NULL,
    cache_age_ms bigint NOT NULL CHECK (cache_age_ms >= 0),
    provider_request_id text,
    json_ld text NOT NULL CHECK (octet_length(json_ld) BETWEEN 2 AND 16384),
    resolved_addresses jsonb NOT NULL
        CHECK (jsonb_typeof(resolved_addresses)='array'
               AND jsonb_array_length(resolved_addresses) BETWEEN 1 AND 32),
    access_policy_ids jsonb NOT NULL
        CHECK (jsonb_typeof(access_policy_ids)='array'
               AND jsonb_array_length(access_policy_ids) BETWEEN 1 AND 16),
    proof_hash char(64) NOT NULL CHECK (proof_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER immutable_evidence_capture_proofs
    BEFORE UPDATE OR DELETE ON evidence_capture_proofs
    FOR EACH ROW EXECUTE FUNCTION de_reject_immutable_mutation();
