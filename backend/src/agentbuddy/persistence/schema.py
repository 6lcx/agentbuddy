from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)

metadata = MetaData()
workers = Table(
    "worker_instances",
    metadata,
    Column("id", String, primary_key=True),
    Column("boot_id", String, nullable=False, unique=True),
    Column("pid", Integer, nullable=False),
    Column("started_at", Float, nullable=False),
    Column("heartbeat_at", Float, nullable=False),
    Column("status", String, nullable=False),
    CheckConstraint("status IN ('running','stopped','lost')", name="ck_worker_status"),
)
operations = Table(
    "operations",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("worker_id", String, ForeignKey("worker_instances.id")),
    Column("claim_token", String),
    Column("claimed_at", Float),
    Column("result_json", JSON),
    Column("error_json", JSON),
    Column("created_at", Float, nullable=False),
    Column("finished_at", Float),
    CheckConstraint(
        "status IN ('queued','running','succeeded','failed')", name="ck_operation_status"
    ),
    Index("ix_operations_queue", "status", "created_at", "id"),
    Index("ix_operations_worker", "worker_id", "status"),
)
events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("operation_id", String, ForeignKey("operations.id"), nullable=False),
    Column("type", String, nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
    Index("ix_events_operation_id", "operation_id", "id"),
    sqlite_autoincrement=True,
)


credentials = Table(
    "credentials",
    metadata,
    Column("id", String, primary_key=True),
    Column("secret", String, nullable=False),
)
providers = Table(
    "providers",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("provider_type", String, nullable=False),
    Column("base_url", String, nullable=False),
    Column("credential_id", String, ForeignKey("credentials.id"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("config_json", JSON, nullable=False),
)
models = Table(
    "models",
    metadata,
    Column("id", String, primary_key=True),
    Column("provider_id", String, ForeignKey("providers.id"), nullable=False),
    Column("model_id", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("context_window", Integer, nullable=False),
    Column("max_output_tokens", Integer, nullable=False),
    Column("test_status", String, nullable=False),
    Column("tested_provider_revision", Integer),
    Column("tested_model_revision", Integer),
    Column("capabilities_json", JSON, nullable=False),
    Column("tested_at", Float),
    UniqueConstraint("provider_id", "model_id", name="uq_provider_model"),
)
settings_table = Table(
    "settings",
    metadata,
    Column("key", String, primary_key=True),
    Column("value_json", JSON, nullable=False),
    Column("revision", Integer, nullable=False),
)
projects = Table(
    "projects",
    metadata,
    Column("id", String, primary_key=True),
    Column("title", String, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
runs = Table(
    "runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, ForeignKey("projects.id"), nullable=False),
    Column("status", String, nullable=False),
    Column("snapshot_json", JSON, nullable=False),
    Column("worker_id", String, ForeignKey("worker_instances.id")),
    Column("claim_token", String),
    Column("partial_text", String, nullable=False),
    Column("failure_json", JSON),
    Column("created_at", Float, nullable=False),
    Column("finished_at", Float),
    CheckConstraint(
        "status IN ('queued','running','succeeded','failed','interrupted')", name="ck_run_status"
    ),
    Index("ix_runs_queue", "status", "created_at"),
    Index(
        "uq_project_active_run",
        "project_id",
        unique=True,
        sqlite_where=text("status IN ('queued','running')"),
    ),
)
messages = Table(
    "messages",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, ForeignKey("projects.id"), nullable=False),
    Column("run_id", String, ForeignKey("runs.id"), nullable=False),
    Column("role", String, nullable=False),
    Column("content", String, nullable=False),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("run_id", "role", name="uq_run_message_role"),
)
request_keys = Table(
    "request_keys",
    metadata,
    Column("key", String, primary_key=True),
    Column("request_hash", String, nullable=False),
    Column("response_json", JSON, nullable=False),
)
run_events = Table(
    "run_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.id"), nullable=False),
    Column("type", String, nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
    Index("ix_run_events_cursor", "run_id", "id"),
    sqlite_autoincrement=True,
)

run_controls = Table(
    "run_controls",
    metadata,
    Column("run_id", String, ForeignKey("runs.id"), primary_key=True),
    Column("calls_limit", Integer, nullable=False),
    Column("calls_used", Integer, nullable=False),
    Column("seconds_limit", Integer, nullable=False),
    Column("elapsed", Float, nullable=False),
    Column("active_since", Float),
)
interactions = Table(
    "interactions",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, ForeignKey("runs.id"), nullable=False),
    Column("digest", String, nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
)
capabilities = Table(
    "capabilities",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("config_json", JSON, nullable=False),
)
assets = Table(
    "assets",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("project_id", String, ForeignKey("projects.id")),
    Column("run_id", String, ForeignKey("runs.id")),
    Column("draft_id", String),
    Column("manifest_json", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
)

run_lanes = Table(
    "run_lanes",
    metadata,
    Column("run_id", String, ForeignKey("runs.id"), primary_key=True),
    Column("id", String, primary_key=True),
    Column("state", String, nullable=False),
)
subtasks = Table(
    "subtasks",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, ForeignKey("runs.id"), nullable=False),
    Column("task_key", String, nullable=False),
    Column("expert_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("plan_json", JSON, nullable=False),
    Column("result_json", JSON),
    Column("started_at", Float),
    Column("finished_at", Float),
    Index("ix_subtasks_run", "run_id"),
)
