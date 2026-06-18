"""
server.py — Benz Club control-plane API.

This process owns no trading logic at all. It is a thin HTTP layer in front of
the same files bot_runner.py already reads/writes:

    config/bots.json     — per-bot settings (read by bot_runner every 2s) +
                            per-bot live status (written by bot_runner)
    data/journal.jsonl    — append-only trade log (written by bot_runner)

index.html talks to this API instead of opening its own connection to Deriv
for trading. Settings saved here are picked up by the already-running
bot_runner.py processes automatically (config hot-reload, no restart).

Run standalone for development:
    uvicorn server:app --host 0.0.0.0 --port 8787 --reload
Normally this is launched by supervisor.py instead, alongside the 4 bot
processes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from filelock import FileLock

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "bots.json"
JOURNAL_PATH = ROOT / "data" / "journal.jsonl"
SUPERVISOR_STATUS_PATH = ROOT / "data" / "supervisor_status.json"
INDEX_HTML_PATH = ROOT / "index.html"
LOCK_PATH = ROOT / "config" / "bots.json.lock"

BOT_IDS = ("even", "odd", "nexus", "differ")

# Settings the UI is allowed to write. Anything else in a PATCH body is
# ignored — this keeps bot_runner.py's status fields (balance, phase, etc.)
# from being clobbered by a stray client write.
WRITABLE_KEYS = {
    "token", "market", "mode", "tradeMode", "strategy", "stake",
    "targetRuns", "minSignal", "minStreak", "overBarrier", "recoveryBarrier",
    "martingaleEnabled", "martingaleMultiplier", "martingaleStopStep",
    "takeProfit", "stopLoss", "autoPilot", "smartRecovery",
    "alternatingRecovery", "alternatingRecoveryB", "tickPeriod", "appId",
}

DEFAULTS = {
    "active": False,
    "token": "",
    "market": "R_100",
    "mode": "evenodd",
    "tradeMode": "even",
    "strategy": 1,
    "stake": 0.35,
    "targetRuns": 4,
    "minSignal": 80,
    "minStreak": 2,
    "overBarrier": 3,
    "recoveryBarrier": 5,
    "martingaleEnabled": False,
    "martingaleMultiplier": 2.1,
    "martingaleStopStep": 3,
    "takeProfit": 15.0,
    "stopLoss": 10.0,
    "autoPilot": True,
    "smartRecovery": False,
    "alternatingRecovery": False,
    "alternatingRecoveryB": False,
    "tickPeriod": 50,
    "appId": "33AsK8F3vLO1plM4tD2Wj",
}

app = FastAPI(title="Benz Club Control API")
_lock = FileLock(str(LOCK_PATH))


# ---------------------------------------------------------------------------
# Low-level file helpers (locked read-modify-write of bots.json)
# ---------------------------------------------------------------------------

def _read_raw() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _write_raw(raw: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2))
    tmp.replace(CONFIG_PATH)


def mutate_bots(fn) -> dict:
    """Locked read -> fn(raw) -> write. fn mutates raw in place. Returns raw."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text("{}")
    with _lock:
        raw = _read_raw()
        fn(raw)
        _write_raw(raw)
        return raw


def get_bot_view(raw: dict, bot_id: str) -> dict:
    """Defaults merged with whatever is on disk for this bot — same shape
    bot_runner.load_config() produces, plus any status fields bot_runner has
    appended (phase, balance, lastTrade, etc.)."""
    cfg = {**DEFAULTS, **raw.get(bot_id, {})}
    return cfg


# ---------------------------------------------------------------------------
# Static page
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    if not INDEX_HTML_PATH.exists():
        raise HTTPException(404, "index.html not found next to server.py")
    return FileResponse(INDEX_HTML_PATH)


# ---------------------------------------------------------------------------
# Supervisor liveness (best-effort; absent if supervisor.py isn't running)
# ---------------------------------------------------------------------------

def _read_supervisor_status() -> dict:
    try:
        return json.loads(SUPERVISOR_STATUS_PATH.read_text())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# State / per-bot endpoints
# ---------------------------------------------------------------------------

@app.get("/api/state")
def get_state():
    raw = _read_raw()
    sup = _read_supervisor_status()
    bots = {bid: get_bot_view(raw, bid) for bid in BOT_IDS}
    has_token = any(bots[bid].get("token") for bid in BOT_IDS)
    for bid in BOT_IDS:
        bots[bid]["_process"] = sup.get(bid, {"alive": None})
    return {"bots": bots, "hasToken": has_token, "serverTime": int(time.time() * 1000)}


@app.get("/api/bots/{bot_id}")
def get_bot(bot_id: str):
    if bot_id not in BOT_IDS:
        raise HTTPException(404, f"unknown bot_id '{bot_id}'")
    raw = _read_raw()
    view = get_bot_view(raw, bot_id)
    sup = _read_supervisor_status()
    view["_process"] = sup.get(bot_id, {"alive": None})
    return view


@app.post("/api/bots/{bot_id}/config")
def patch_bot_config(bot_id: str, patch: dict[str, Any]):
    if bot_id not in BOT_IDS:
        raise HTTPException(404, f"unknown bot_id '{bot_id}'")
    clean = {k: v for k, v in patch.items() if k in WRITABLE_KEYS}
    if not clean:
        raise HTTPException(400, "no recognised settings in request body")

    # Only one recovery mode can be active at a time — mirror the UI's
    # mutually-exclusive recovery-mode cards.
    recovery_flags = {"smartRecovery", "alternatingRecovery", "alternatingRecoveryB"}
    touched_recovery = recovery_flags & clean.keys()
    if touched_recovery:
        for f in recovery_flags:
            clean.setdefault(f, False)
        # whichever flag the caller set True wins; if caller sent multiple
        # True values (shouldn't happen from the UI) the first wins.
        true_flags = [f for f in recovery_flags if clean.get(f)]
        for f in recovery_flags:
            clean[f] = f in true_flags[:1]

    def _apply(raw: dict):
        raw.setdefault(bot_id, {})
        raw[bot_id].update(clean)

    raw = mutate_bots(_apply)
    return get_bot_view(raw, bot_id)


@app.post("/api/bots/{bot_id}/start")
def start_bot(bot_id: str):
    if bot_id not in BOT_IDS:
        raise HTTPException(404, f"unknown bot_id '{bot_id}'")
    raw = _read_raw()
    cfg = get_bot_view(raw, bot_id)
    if not cfg.get("token"):
        raise HTTPException(400, "no Deriv API token configured — set one in Settings first")

    def _apply(raw: dict):
        raw.setdefault(bot_id, {})
        raw[bot_id]["active"] = True

    raw = mutate_bots(_apply)
    return get_bot_view(raw, bot_id)


@app.post("/api/bots/{bot_id}/stop")
def stop_bot(bot_id: str):
    if bot_id not in BOT_IDS:
        raise HTTPException(404, f"unknown bot_id '{bot_id}'")

    def _apply(raw: dict):
        raw.setdefault(bot_id, {})
        raw[bot_id]["active"] = False

    raw = mutate_bots(_apply)
    return get_bot_view(raw, bot_id)


# ---------------------------------------------------------------------------
# Shared token — "one token powers the whole site"
# ---------------------------------------------------------------------------

@app.post("/api/token")
def set_token(body: dict[str, Any]):
    token = str(body.get("token", "")).strip()
    app_id = str(body.get("appId", "33AsK8F3vLO1plM4tD2Wj")).strip()

    def _apply(raw: dict):
        for bid in BOT_IDS:
            raw.setdefault(bid, {})
            raw[bid]["token"] = token
            raw[bid]["appId"] = app_id

    mutate_bots(_apply)
    return {"ok": True, "hasToken": bool(token), "appId": app_id}


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def _iter_journal():
    if not JOURNAL_PATH.exists():
        return
    with JOURNAL_PATH.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


@app.get("/api/journal")
def get_journal(bot_id: Optional[str] = Query(default=None), limit: int = Query(default=200, le=2000)):
    entries = list(_iter_journal())
    if bot_id:
        entries = [e for e in entries if e.get("botId") == bot_id]
    entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return entries[:limit]


@app.delete("/api/journal")
def clear_journal(bot_id: Optional[str] = Query(default=None)):
    if not JOURNAL_PATH.exists():
        return {"ok": True, "cleared": 0}
    with _lock:
        if bot_id:
            kept = [e for e in _iter_journal() if e.get("botId") != bot_id]
            removed = sum(1 for _ in _iter_journal()) - len(kept)
            tmp = JOURNAL_PATH.with_suffix(".tmp")
            with tmp.open("w") as f:
                for e in kept:
                    f.write(json.dumps(e) + "\n")
            tmp.replace(JOURNAL_PATH)
        else:
            removed = sum(1 for _ in _iter_journal())
            JOURNAL_PATH.write_text("")
    return {"ok": True, "cleared": removed}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    sup = _read_supervisor_status()
    return {"status": "ok", "bots": sup, "serverTime": int(time.time() * 1000)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8787)
