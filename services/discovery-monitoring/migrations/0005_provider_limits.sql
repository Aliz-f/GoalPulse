CREATE TABLE provider_usage_day (
    day date PRIMARY KEY,
    operations integer NOT NULL DEFAULT 0 CHECK (operations >= 0),
    confirmed_credits bigint NOT NULL DEFAULT 0 CHECK (confirmed_credits >= 0),
    reserved_credits bigint NOT NULL DEFAULT 0 CHECK (reserved_credits >= 0),
    pending_operations integer NOT NULL DEFAULT 0 CHECK (pending_operations >= 0),
    unknown_usage_operations integer NOT NULL DEFAULT 0 CHECK (unknown_usage_operations >= 0)
);
CREATE TABLE provider_resource_state (
    resource_key char(64) PRIMARY KEY,
    next_allowed_at timestamptz NOT NULL,
    last_error_code text
);
CREATE TABLE provider_operations (
    operation_id uuid PRIMARY KEY,
    day date NOT NULL REFERENCES provider_usage_day(day),
    resource_key char(64) NOT NULL REFERENCES provider_resource_state(resource_key),
    estimated_credits integer NOT NULL CHECK (estimated_credits > 0),
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    confirmed_credits integer CHECK (confirmed_credits >= 0),
    error_code text
);
