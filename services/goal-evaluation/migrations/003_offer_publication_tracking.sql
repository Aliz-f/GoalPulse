-- Apply after 001/002 in Goal Evaluation's owned PostgreSQL database.
BEGIN;
ALTER TABLE offer_fingerprints ADD COLUMN verification_event_id uuid;
CREATE UNIQUE INDEX offer_verification_event ON offer_fingerprints(verification_event_id)
    WHERE verification_event_id IS NOT NULL;
-- New reservations use notification_emitted=false until a broker confirmation.
-- Retain legacy NULL-pointer rows conservatively: older releases used true for
-- reservation intent, not confirmation. Audit their outbox before any manual
-- repair; do not guess whether an old verification reached downstream consumers.
COMMIT;
