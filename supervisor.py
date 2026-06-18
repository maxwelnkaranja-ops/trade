"""
supervisor.py — single entry point for the whole backend.

Run this once, leave it running:

    python supervisor.py

It will:
  1. Start the control-plane API (server.py's FastAPI app, via uvicorn) so
     index.html has something to talk to.
  2. Spawn the four persistent trading loops:
         python agents/bot_runner.py --bot even
         python agents/bot_runner.py --bot odd
         python agents/bot_runner.py --bot nexus
         python agents/bot_runner.py --bot differ
     and keep them alive forever — if one crashes it is restarted with
     exponential backoff. Settings changes made through the UI are picked
     up by bot_runner.py's own config hot-reload; nothing here needs to be
     restarted when you change a stake or a strategy.
  3. Write data/supervisor_status.json every few seconds so server.py can
     report which bots are actually alive (separate from "active": a bot
     can be alive but idle/waiting, or — if its process died — not alive
     at all regardless of what bots.json says).

Stop with Ctrl+C. All four bot processes and the API server are terminated
cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"
SUPERVISOR_STATUS_PATH = DATA_DIR / "supervisor_status.json"

BOT_IDS = ("even", "odd", "nexus", "differ")
RESTART_DELAY = 3
MAX_RESTART_DELAY = 60
STATUS_WRITE_INTERVAL = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("supervisor")


class BotProcess:
    """Supervises one `bot_runner.py --bot <id>` subprocess forever."""

    def __init__(self, bot_id: str):
        self.bot_id = bot_id
        self.proc: asyncio.subprocess.Process | None = None
        self.restarts = 0
        self.last_start = 0.0
        self.last_exit_code: int | None = None
        self.delay = RESTART_DELAY
        self._stopping = False

    def snapshot(self) -> dict:
        return {
            "pid": self.proc.pid if self.proc and self.proc.returncode is None else None,
            "alive": bool(self.proc and self.proc.returncode is None),
            "restarts": self.restarts,
            "lastStart": self.last_start,
            "lastExitCode": self.last_exit_code,
        }

    async def run_forever(self, start_delay: float = 0):
        log_path = LOGS_DIR / f"{self.bot_id}.log"
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        # Stagger initial start
        if start_delay > 0:
            log.info(f"Bot {self.bot_id} waiting {start_delay}s before starting...")
            await asyncio.sleep(start_delay)
        while not self._stopping:
            log.info(f"starting bot_runner --bot {self.bot_id}")
            self.last_start = time.time()
            with log_path.open("a") as logf:
                logf.write(f"\n===== supervisor start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                logf.flush()
                self.proc = await asyncio.create_subprocess_exec(
                    sys.executable, str(ROOT / "bot_runner.py"), "--bot", self.bot_id,
                    cwd=str(ROOT),
                    stdout=logf, stderr=logf,
                )
            try:
                rc = await self.proc.wait()
            except asyncio.CancelledError:
                self._stopping = True
                await self._terminate()
                raise
            self.last_exit_code = rc
            if self._stopping:
                break
            log.warning(f"bot_runner --bot {self.bot_id} exited (code {rc}) — "
                        f"restarting in {self.delay}s")
            self.restarts += 1
            await asyncio.sleep(self.delay)
            self.delay = min(self.delay * 1.5, MAX_RESTART_DELAY)
            if rc == 0:
                self.delay = RESTART_DELAY  # clean exit, don't punish next start

    async def _terminate(self):
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
            except ProcessLookupError:
                pass

    async def stop(self):
        self._stopping = True
        await self._terminate()


async def write_status_loop(bots: dict[str, BotProcess]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        status = {bid: b.snapshot() for bid, b in bots.items()}
        try:
            tmp = SUPERVISOR_STATUS_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(status, indent=2))
            tmp.replace(SUPERVISOR_STATUS_PATH)
        except Exception as e:
            log.warning(f"could not write supervisor status: {e}")
        await asyncio.sleep(STATUS_WRITE_INTERVAL)


async def run_api_server(host: str, port: int):
    import uvicorn
    sys.path.insert(0, str(ROOT))
    from server import app  # noqa: local import so ROOT is on sys.path first

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    log.info(f"API server + UI listening on http://{host}:{port}")
    await server.serve()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-bots", action="store_true",
                        help="only run the API server, don't spawn trading bots (debugging)")
    parser.add_argument("--no-server", action="store_true",
                        help="only run the trading bots, don't start the API server")
    args = parser.parse_args()

    (ROOT / "config").mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    bots_json = ROOT / "config" / "bots.json"
    if not bots_json.exists():
        bots_json.write_text("{}")

    tasks: list[asyncio.Task] = []
    bots: dict[str, BotProcess] = {}

    if not args.no_bots:
        # Stagger bot starts with 15-second delays to avoid rate limits
        stagger_delays = [0, 15, 30, 45]
        for i, bid in enumerate(BOT_IDS):
            bp = BotProcess(bid)
            bots[bid] = bp
            delay = stagger_delays[i]
            tasks.append(asyncio.create_task(bp.run_forever(delay), name=f"bot:{bid}"))
        tasks.append(asyncio.create_task(write_status_loop(bots), name="status-writer"))

    if not args.no_server:
        tasks.append(asyncio.create_task(run_api_server(args.host, args.port), name="api-server"))

    if not tasks:
        log.error("Nothing to run — both --no-bots and --no-server were set")
        return

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop():
        log.info("shutdown requested...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, AttributeError):
            # Windows doesn't support add_signal_handler for SIGTERM
            pass

    await stop_event.wait()

    log.info("stopping all processes...")
    for bp in bots.values():
        await bp.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
