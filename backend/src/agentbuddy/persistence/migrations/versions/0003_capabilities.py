"""Run controls and capability/file records."""

from alembic import op

revision = "0003_capabilities"
down_revision = "0002_chat"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
CREATE TABLE run_controls (
	run_id VARCHAR NOT NULL, 
	calls_limit INTEGER NOT NULL, 
	calls_used INTEGER NOT NULL, 
	seconds_limit INTEGER NOT NULL, 
	elapsed FLOAT NOT NULL, 
	active_since FLOAT, 
	PRIMARY KEY (run_id), 
	FOREIGN KEY(run_id) REFERENCES runs (id)
)

"""
    )
    op.execute(
        """
CREATE TABLE interactions (
	id VARCHAR NOT NULL, 
	run_id VARCHAR NOT NULL, 
	digest VARCHAR NOT NULL, 
	payload_json JSON NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at FLOAT NOT NULL, 
	expires_at FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES runs (id)
)

"""
    )
    op.execute(
        """
CREATE TABLE capabilities (
	id VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	revision INTEGER NOT NULL, 
	config_json JSON NOT NULL, 
	PRIMARY KEY (id)
)

"""
    )
    op.execute(
        """
CREATE TABLE assets (
	id VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	project_id VARCHAR, 
	run_id VARCHAR, 
	draft_id VARCHAR, 
	manifest_json JSON NOT NULL, 
	created_at FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(run_id) REFERENCES runs (id)
)

"""
    )

    op.execute("INSERT INTO run_controls SELECT id, 120, 0, 1800, 0, NULL FROM runs")


def downgrade():
    raise RuntimeError("Restore a complete backup instead")
