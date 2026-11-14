-- Apply only to Agent Integration's owned PostgreSQL database.
BEGIN;
CREATE TABLE inbox (
    event_id uuid PRIMARY KEY,
    processed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE action_packages (
    package_id uuid PRIMARY KEY,
    goal_id uuid NOT NULL,
    goal_version integer NOT NULL CHECK (goal_version>0),
    evaluation_id uuid NOT NULL,
    owner_user_id uuid NOT NULL,
    offer_fingerprint text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at>created_at),
    correlation_id uuid NOT NULL,
    UNIQUE (goal_id,goal_version,evaluation_id),
    UNIQUE (goal_id,goal_version,offer_fingerprint),
    CHECK (payload @> '{"approval_required":true,"auto_execute":false,"requires_checkout_revalidation":true,"next_action":"request_user_approval","action_type":"prepare_purchase"}'::jsonb)
);
CREATE INDEX action_packages_owner ON action_packages(owner_user_id,created_at DESC);
CREATE FUNCTION reject_package_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Action packages are immutable';
END;
$$;
CREATE TRIGGER action_packages_immutable BEFORE UPDATE OR DELETE ON action_packages
    FOR EACH ROW EXECUTE FUNCTION reject_package_mutation();
COMMIT;
