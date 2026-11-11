"""Collaboration tasks and aggregate activity lanes."""

from alembic import op

revision = "0004_collaboration"
down_revision = "0003_capabilities"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE run_lanes (
        run_id VARCHAR NOT NULL REFERENCES runs(id),
        id VARCHAR NOT NULL, state VARCHAR NOT NULL,
        PRIMARY KEY(run_id, id))""")
    op.execute("""CREATE TABLE subtasks (
        id VARCHAR PRIMARY KEY NOT NULL, run_id VARCHAR NOT NULL REFERENCES runs(id),
        task_key VARCHAR NOT NULL, expert_id VARCHAR NOT NULL, status VARCHAR NOT NULL,
        plan_json JSON NOT NULL, result_json JSON,
        started_at FLOAT, finished_at FLOAT)""")
    op.create_index("ix_subtasks_run", "subtasks", ["run_id"])


def downgrade():
    raise RuntimeError("Restore a complete backup")
