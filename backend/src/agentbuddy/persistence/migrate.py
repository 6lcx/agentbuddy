import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path

from alembic import command
from alembic.config import Config
from filelock import FileLock

from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database

HEAD = "0004_collaboration"


def upgrade(path: Path, revision: str = "head"):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # API and Worker must both be offline for schema changes.
    with ExitStack() as stack:
        for suffix in (".api.lock", ".worker.lock"):
            stack.enter_context(FileLock(str(path) + suffix, timeout=0))
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        database = Database(path)
        try:
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
            with database.write() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, revision)
        finally:
            database.close()


def main():
    settings = Settings()
    settings.prepare_directories()
    upgrade(settings.database_path)
    print(f"Database migrated to {HEAD}: {settings.database_path}")


if __name__ == "__main__":
    main()
