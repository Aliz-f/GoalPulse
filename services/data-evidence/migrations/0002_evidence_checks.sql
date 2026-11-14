-- R2-D3-02: append-only checker decisions and immutable derived observations.
-- Originals remain addressable by ID; source reads omit superseded snapshots.

CREATE TABLE evidence_checks (
    check_id uuid PRIMARY KEY,
    original_observation_id uuid NOT NULL REFERENCES observations (observation_id),
    result_observation_id uuid REFERENCES observations (observation_id),
    decision text NOT NULL CHECK (decision IN ('checked','unverified','disputed')),
    reasons jsonb NOT NULL CHECK (jsonb_typeof(reasons) = 'array'),
    policy_version text NOT NULL,
    normalizer_version text NOT NULL,
    request_fingerprint char(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    input_digest char(64) NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    proof_digest char(64) CHECK (proof_digest ~ '^[0-9a-f]{64}$'),
    proof jsonb NOT NULL CHECK (jsonb_typeof(proof) = 'object'),
    correlation_id uuid NOT NULL,
    checked_at timestamptz NOT NULL,
    CHECK (result_observation_id IS NULL OR result_observation_id <> original_observation_id),
    CHECK (decision <> 'checked' OR (result_observation_id IS NOT NULL AND proof_digest IS NOT NULL)),
    CHECK (octet_length(proof::text) <= 65536)
);

CREATE UNIQUE INDEX evidence_checks_checked_proof ON evidence_checks (proof_digest)
    WHERE decision = 'checked';
CREATE INDEX evidence_checks_original ON evidence_checks (original_observation_id)
    WHERE result_observation_id IS NOT NULL;
CREATE INDEX evidence_checks_time ON evidence_checks (checked_at, check_id);

CREATE TRIGGER immutable_evidence_checks BEFORE UPDATE OR DELETE ON evidence_checks
    FOR EACH ROW EXECUTE FUNCTION de_reject_immutable_mutation();
