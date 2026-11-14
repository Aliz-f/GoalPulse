-- Apply after 002 to the notification-owned database only.
-- Existing notifications count towards the sliding rate window.
BEGIN;
CREATE INDEX notifications_owner_goal_created
    ON notifications (user_id, goal_id, created_at DESC)
    WHERE channel = 'in_app';
COMMIT;
