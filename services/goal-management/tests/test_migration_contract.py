from pathlib import Path
ROOT=Path(__file__).parents[1]
def test_migration_has_owned_tables_and_reversible_downgrade():
    text=(ROOT/'alembic/versions/0001_goal_management_schema.py').read_text()
    for table in ('goals','goal_versions','goal_evaluation_projection','inbox','outbox'):
        assert f'CREATE TABLE {table}' in text and f'DROP TABLE {table}' in text
    assert 'confirmed_session_id UUID NOT NULL UNIQUE' in text
    assert 'PRIMARY KEY (goal_id, version)' in text
