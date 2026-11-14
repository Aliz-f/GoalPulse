-- Apply once only to the Notification-owned PostgreSQL database.
BEGIN;
CREATE TABLE preferences (
    user_id uuid PRIMARY KEY,
    in_app_enabled boolean NOT NULL DEFAULT true,
    email_enabled boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE notifications (
    notification_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    goal_id uuid NOT NULL,
    goal_version integer NOT NULL CHECK (goal_version > 0),
    evaluation_id uuid NOT NULL,
    offer_fingerprint text NOT NULL,
    channel text NOT NULL CHECK (channel = 'in_app'),
    title text NOT NULL,
    summary text NOT NULL,
    evaluated_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    correlation_id uuid NOT NULL,
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'array'),
    action_package_id uuid,
    read_at timestamptz,
    status text NOT NULL DEFAULT 'unread' CHECK (status IN ('unread','read')),
    CHECK ((status='unread' AND read_at IS NULL) OR (status='read' AND read_at IS NOT NULL)),
    UNIQUE (user_id,goal_id,goal_version,offer_fingerprint,channel)
);
CREATE INDEX notifications_user_created ON notifications (user_id,created_at DESC,notification_id);
CREATE TABLE deliveries (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    notification_id uuid NOT NULL REFERENCES notifications(notification_id),
    channel text NOT NULL CHECK (channel='in_app'),
    state text NOT NULL CHECK (state IN ('available','failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
    next_retry_at timestamptz,
    provider_message_id text,
    UNIQUE (notification_id,channel)
);
CREATE TABLE inbox (
    event_id uuid PRIMARY KEY,
    processed_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
