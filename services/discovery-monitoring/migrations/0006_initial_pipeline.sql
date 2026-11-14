-- R2-D3-01: resume initial discovery and exact HTTP delivery after worker crashes.
CREATE TABLE discovery_plans (
    goal_id uuid NOT NULL,
    goal_version integer NOT NULL CHECK (goal_version > 0),
    urls jsonb NOT NULL CHECK (jsonb_typeof(urls) = 'array' AND jsonb_array_length(urls) <= 5),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (goal_id, goal_version)
);

CREATE TABLE extraction_deliveries (
    attempt_id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES sources(source_id),
    submission jsonb NOT NULL CHECK (jsonb_typeof(submission) = 'object'),
    submission_hash char(64) NOT NULL,
    capture_metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(capture_metadata) = 'object'),
    receipt jsonb CHECK (receipt IS NULL OR jsonb_typeof(receipt) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    CHECK (submission->>'attempt_id' = attempt_id::text),
    CHECK (submission->>'source_id' = source_id::text),
    CHECK ((receipt IS NULL) = (delivered_at IS NULL))
);

CREATE FUNCTION dm_protect_pipeline_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR TG_TABLE_NAME = 'discovery_plans' THEN
        RAISE EXCEPTION 'Initial discovery records are immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
       OR NEW.source_id IS DISTINCT FROM OLD.source_id
       OR NEW.submission IS DISTINCT FROM OLD.submission
       OR NEW.submission_hash IS DISTINCT FROM OLD.submission_hash
       OR NEW.capture_metadata IS DISTINCT FROM OLD.capture_metadata
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR OLD.receipt IS NOT NULL THEN
        RAISE EXCEPTION 'Extraction delivery intent is immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_discovery_plans BEFORE UPDATE OR DELETE ON discovery_plans
    FOR EACH ROW EXECUTE FUNCTION dm_protect_pipeline_history();
CREATE TRIGGER protected_extraction_deliveries BEFORE UPDATE OR DELETE ON extraction_deliveries
    FOR EACH ROW EXECUTE FUNCTION dm_protect_pipeline_history();
