import asyncio
import logging
import tempfile
from collections.abc import Awaitable, Callable

from filelock import FileLock
from sqlalchemy import text

from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.persistence.repository import QueueRepository
from agentbuddy.runtime.executor import Executor
from agentbuddy.runtime.repository import RunRepository

logger = logging.getLogger(__name__)
Handler = Callable[[dict], Awaitable[dict]]


class Worker:
    def __init__(self, settings: Settings, handlers: dict[str, Handler] | None = None):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.repo = QueueRepository(self.db)
        self.executor = Executor(self.db)
        self.run_repo = RunRepository(self.db)
        self.handlers = (
            handlers
            if handlers is not None
            else {
                "storage_check": self.storage_check,
                "model_probe": self.executor.model_probe,
                "connector_probe": self.connector_probe,
                "ingest_upload": self.ingest_upload,
                "skill_import": self.skill_import,
                "connector_import": self.connector_import,
            }
        )
        self.worker_id = None

    async def ingest_upload(self, payload):
        from agentbuddy.runtime.files import Files

        source = self.settings.data_dir / "staging" / payload["staged_id"]
        try:
            return await asyncio.to_thread(
                Files(self.db).upload, payload["name"], source.read_bytes(), payload["draft_id"]
            )
        finally:
            source.unlink(missing_ok=True)

    async def skill_import(self, payload):
        from agentbuddy.catalog.capabilities import Capabilities

        source = self.settings.data_dir / "staging" / payload["staged_id"]
        try:
            return await asyncio.to_thread(
                Capabilities(self.db).install, source.read_bytes(), payload.get("skill_id")
            )
        finally:
            source.unlink(missing_ok=True)

    async def connector_import(self, payload):
        from agentbuddy.catalog.capabilities import Capabilities

        source = self.settings.data_dir / "staging" / payload["staged_id"]
        try:
            return await asyncio.to_thread(
                Capabilities(self.db).import_connector, source.read_bytes()
            )
        finally:
            source.unlink(missing_ok=True)

    async def connector_probe(self, payload):
        from agentbuddy.catalog.capabilities import Capabilities
        from agentbuddy.runtime.tools import discover

        tools = await discover(payload["config_json"])
        return Capabilities(self.db).save(
            "connector",
            {**payload["config_json"], "tools": tools},
            payload["id"],
            payload["revision"],
        )

    async def storage_check(self, payload):
        def check():
            # A real read/write probe; scoped to a disposable file in the platform tmp directory.
            with tempfile.TemporaryFile(dir=self.settings.data_dir / "tmp") as probe:
                probe.write(b"agentbuddy")
                probe.seek(0)
                if probe.read() != b"agentbuddy":
                    raise OSError("Storage readback mismatch")
            with self.db.engine.connect() as conn:
                mode = conn.scalar(text("PRAGMA journal_mode"))
                foreign_keys = conn.scalar(text("PRAGMA foreign_keys"))
            return {"storage": "ok", "journal_mode": mode, "foreign_keys": bool(foreign_keys)}

        return await asyncio.to_thread(check)

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(self.settings.worker_heartbeat_seconds)
            if not await asyncio.to_thread(self.repo.heartbeat, self.worker_id):
                raise RuntimeError("Worker registration lost")

    async def _consume(self, stop: asyncio.Event, once: bool):
        while not stop.is_set():
            claimed = await asyncio.to_thread(self.repo.claim, self.worker_id)
            if claimed:
                handler = self.handlers.get(claimed["kind"])
                result = None
                error = None
                if handler is None:
                    error = {"code": "unsupported_operation", "message": "No handler registered"}
                else:
                    try:
                        result = await asyncio.wait_for(
                            handler(claimed["payload_json"]),
                            self.settings.operation_timeout_seconds,
                        )
                    except TimeoutError:
                        error = {
                            "code": "operation_timeout",
                            "message": "Operation exceeded time limit",
                        }
                    except Exception as exc:
                        from agentbuddy.catalog.service import DomainError

                        logger.exception("Operation failed: %s", claimed["id"])
                        error = {
                            "code": exc.code
                            if isinstance(exc, DomainError)
                            else "operation_failed",
                            "message": exc.message
                            if isinstance(exc, DomainError)
                            else "Operation failed; see Worker log",
                        }
                await asyncio.to_thread(self.repo.finish, claimed, result=result, error=error)
            if once:
                return
            if not claimed:
                try:
                    await asyncio.wait_for(stop.wait(), self.settings.worker_poll_seconds)
                except TimeoutError:
                    pass

    async def _schedule_runs(self, stop, once):
        async with asyncio.TaskGroup() as group:
            tasks = set()
            launched = 0
            while not stop.is_set():
                tasks = {task for task in tasks if not task.done()}
                claimed = (
                    None
                    if once and launched >= 2
                    else await asyncio.to_thread(self.run_repo.claim, self.worker_id)
                )
                if claimed:
                    tasks.add(group.create_task(self.executor.execute(claimed)))
                    launched += 1
                elif once:
                    break
                else:
                    await asyncio.sleep(self.settings.worker_poll_seconds)

    async def _consume_all(self, stop, once):
        async with asyncio.TaskGroup() as group:
            group.create_task(self._consume(stop, once))
            group.create_task(self._schedule_runs(stop, once))

    async def run(self, stop: asyncio.Event | None = None, *, once=False):
        stop = stop or asyncio.Event()
        self.settings.prepare_directories()
        lock = FileLock(str(self.settings.database_path) + ".worker.lock", timeout=0)
        try:
            with lock:
                self.db.require_schema()
                from agentbuddy.runtime.files import Files

                await asyncio.to_thread(Files(self.db).expire_drafts)
                from agentbuddy.catalog.capabilities import Capabilities

                await asyncio.to_thread(Capabilities(self.db).seed_presets)
                # Exclusive OS lock proves no previous scheduler still owns execution.
                await asyncio.to_thread(self.repo.recover_previous_workers)
                await asyncio.to_thread(self.run_repo.interrupt)
                self.worker_id = await asyncio.to_thread(self.repo.start_worker)
                logger.info("Worker started: %s", self.worker_id)
                consumer = asyncio.create_task(self._consume_all(stop, once))
                heartbeat = asyncio.create_task(self._heartbeat())
                stopping = asyncio.create_task(stop.wait())
                try:
                    done, _ = await asyncio.wait(
                        [consumer, heartbeat, stopping], return_when=asyncio.FIRST_COMPLETED
                    )
                    if heartbeat in done:
                        heartbeat.result()  # Stop if heartbeats fail.
                    if consumer in done:
                        consumer.result()
                finally:
                    stop.set()
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(consumer), self.settings.worker_shutdown_seconds
                        )
                    except (TimeoutError, asyncio.CancelledError):
                        consumer.cancel()
                    finally:
                        heartbeat.cancel()
                        stopping.cancel()
                        await asyncio.gather(consumer, heartbeat, stopping, return_exceptions=True)
                        await asyncio.to_thread(self.run_repo.interrupt, self.worker_id)
                        await asyncio.to_thread(self.repo.stop_worker, self.worker_id)
                        logger.info("Worker stopped: %s", self.worker_id)
        finally:
            self.db.close()
