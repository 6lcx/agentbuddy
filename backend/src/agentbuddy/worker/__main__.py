import argparse
import asyncio
import logging
import signal

from filelock import Timeout

from agentbuddy.config import Settings
from agentbuddy.worker.service import Worker


async def serve(once):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await Worker(Settings()).run(stop, once=once)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description="AgentBuddy independent Operation Worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued Operation")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        from agentbuddy.worker.process_tree import contain_process_tree

        contain_process_tree()
        asyncio.run(serve(args.once))
    except Timeout:
        parser.exit(2, "Worker already running or database migration in progress.")
    except Exception as exc:
        logging.error("Worker startup/execution failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
