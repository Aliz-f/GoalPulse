-- Apply after 001_initial.sql, only in the Goal Evaluation owned database.
BEGIN;
CREATE FUNCTION reject_evaluation_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Goal Evaluation records are immutable';
END;
$$;
CREATE TRIGGER evaluations_immutable BEFORE UPDATE OR DELETE ON evaluations
    FOR EACH ROW EXECUTE FUNCTION reject_evaluation_mutation();
CREATE TRIGGER condition_results_immutable BEFORE UPDATE OR DELETE ON condition_results
    FOR EACH ROW EXECUTE FUNCTION reject_evaluation_mutation();
COMMIT;
