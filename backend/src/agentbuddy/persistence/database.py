from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL, create_engine, event, text
from sqlalchemy.pool import NullPool


class Database:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(self.path)),
            connect_args={"check_same_thread": False, "timeout": 5},
            poolclass=NullPool,
        )

        @event.listens_for(self.engine, "connect")
        def configure(connection, _):
            connection.isolation_level = None
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        @event.listens_for(self.engine, "begin")
        def begin(connection):
            connection.exec_driver_sql(
                "BEGIN IMMEDIATE"
                if connection.get_execution_options().get("immediate")
                else "BEGIN"
            )

    @contextmanager
    def write(self):
        with self.engine.connect().execution_options(immediate=True) as connection:
            with connection.begin():
                yield connection

    def require_schema(self):
        from agentbuddy.persistence.migrate import HEAD

        if not self.path.exists():
            raise RuntimeError(
                "Database not initialized; run python -m agentbuddy.persistence.migrate"
            )
        with self.engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != HEAD:
            raise RuntimeError(
                "Database revision mismatch; run python -m agentbuddy.persistence.migrate"
            )

    def close(self):
        self.engine.dispose()
