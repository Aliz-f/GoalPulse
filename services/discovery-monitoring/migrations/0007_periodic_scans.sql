-- R2-D3-03: owned recurrence, operator idempotency and per-user HTTP budgets.
CREATE TABLE scan_schedules (
    goal_id uuid PRIMARY KEY,
    goal_version integer NOT NULL CHECK (goal_version > 0),
    owner_user_id uuid NOT NULL,
    interval_minutes integer NOT NULL CHECK (interval_minutes > 0),
    state text NOT NULL CHECK (state IN ('active','blocked','inactive')),
    reason text,
    next_due_at timestamptz,
    last_checked_at timestamptz NOT NULL,
    last_result text
);
CREATE INDEX scan_schedules_owner ON scan_schedules(owner_user_id,state);
CREATE INDEX scan_schedules_due ON scan_schedules(next_due_at) WHERE state='active';

CREATE TABLE scheduled_scans (
    scan_id uuid PRIMARY KEY,
    goal_id uuid NOT NULL REFERENCES scan_schedules(goal_id),
    goal_version integer NOT NULL CHECK (goal_version > 0),
    correlation_id uuid NOT NULL,
    due_at timestamptz NOT NULL,
    kind text NOT NULL CHECK (kind IN ('periodic','operator')),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL,
    deadline_at timestamptz NOT NULL,
    last_result text
);
CREATE UNIQUE INDEX scheduled_scans_one_pending ON scheduled_scans(goal_id,goal_version) WHERE status='pending';
CREATE INDEX scheduled_scans_due ON scheduled_scans(next_attempt_at) WHERE status='pending';

CREATE TABLE scan_plans (
    scan_id uuid PRIMARY KEY REFERENCES scheduled_scans(scan_id),
    urls jsonb NOT NULL CHECK (jsonb_typeof(urls)='array' AND jsonb_array_length(urls)<=3)
);
CREATE TABLE operator_scan_requests (
    request_id uuid PRIMARY KEY,
    principal text NOT NULL,
    goal_id uuid NOT NULL,
    goal_version integer NOT NULL,
    scan_id uuid NOT NULL,
    requested_at timestamptz NOT NULL
);
CREATE FUNCTION dm_reject_scan_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Scan plans and operator requests are immutable' USING ERRCODE='55000';
END;
$$;
CREATE TRIGGER immutable_scan_plans BEFORE UPDATE OR DELETE ON scan_plans
    FOR EACH ROW EXECUTE FUNCTION dm_reject_scan_history_mutation();
CREATE TRIGGER immutable_operator_requests BEFORE UPDATE OR DELETE ON operator_scan_requests
    FOR EACH ROW EXECUTE FUNCTION dm_reject_scan_history_mutation();

CREATE TABLE provider_user_day (
    owner_user_id uuid NOT NULL,
    day date NOT NULL,
    operations integer NOT NULL DEFAULT 0 CHECK (operations>=0 AND operations<=100),
    PRIMARY KEY(owner_user_id,day)
);
ALTER TABLE provider_operations ADD COLUMN owner_user_id uuid;
