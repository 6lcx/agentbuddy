"""Initial durable Operation queue. Frozen schema, independent of application metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0001_queue"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "worker_instances",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("boot_id", sa.String(), nullable=False, unique=True),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Float(), nullable=False),
        sa.Column("heartbeat_at", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.CheckConstraint("status IN ('running','stopped','lost')", name="ck_worker_status"),
    )
    op.create_table(
        "operations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("worker_id", sa.String(), sa.ForeignKey("worker_instances.id")),
        sa.Column("claim_token", sa.String()),
        sa.Column("claimed_at", sa.Float()),
        sa.Column("result_json", sa.JSON()),
        sa.Column("error_json", sa.JSON()),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("finished_at", sa.Float()),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed')", name="ck_operation_status"
        ),
    )
    op.create_index("ix_operations_queue", "operations", ["status", "created_at", "id"])
    op.create_index("ix_operations_worker", "operations", ["worker_id", "status"])
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("operation_id", sa.String(), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_events_operation_id", "events", ["operation_id", "id"])


def downgrade():
    raise RuntimeError("Restore a full backup instead of destructive downgrade")
