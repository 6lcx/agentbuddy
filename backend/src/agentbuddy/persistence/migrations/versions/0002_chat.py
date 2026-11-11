"""Model catalog and durable conversation runs."""

import sqlalchemy as sa
from alembic import op

revision = "0002_chat"
down_revision = "0001_queue"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "credentials",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("secret", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "request_keys",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "providers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("provider_type", sa.String(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=False),
        sa.Column("credential_id", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("worker_id", sa.String(), nullable=True),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("partial_text", sa.String(), nullable=False),
        sa.Column("failure_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','interrupted')",
            name="ck_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["worker_instances.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_queue", "runs", ["status", "created_at"], unique=False)
    op.create_index(
        "uq_project_active_run",
        "runs",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued','running')"),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "role", name="uq_run_message_role"),
    )
    op.create_table(
        "models",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("test_status", sa.String(), nullable=False),
        sa.Column("tested_provider_revision", sa.Integer(), nullable=True),
        sa.Column("tested_model_revision", sa.Integer(), nullable=True),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("tested_at", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_provider_model"),
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_run_events_cursor", "run_events", ["run_id", "id"], unique=False)


def downgrade():
    raise RuntimeError("Restore a full backup instead of destructive downgrade")
