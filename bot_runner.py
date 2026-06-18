"""
bot_runner.py — One persistent 24/7 bot loop.

Each bot is parameterised by:
  - bot_id: "even" | "odd" | "nexus" | "differ"
  - config read live from config/bots.json every CONFIG_POLL_INTERVAL seconds

The loop:
  1. Connects to Deriv via DerivClient.
  2. Subscribes to the configured market.
  3. On every tick, runs the configured strategy's checkEntry (or
     Normal-Mode conditions).
  4. If conditions are met and bot is active → fires a chain execution
     (buy → wait for result → martingale/recovery → repeat until
     targetRuns, TP, SL, or martingale-stop).
  5. After a chain completes → random cooldown → re-arm.
  6. Config file changes (stake, strategy, TP/SL, active flag) are picked
     up within CONFIG_POLL_INTERVAL seconds, no restart needed.

Run directly:
  python bot_runner.py --bot even
Or managed by supervisor.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

# Make sure sibling imports work wherever this is run from.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from deriv_client import DerivClient
from engine_core import (
    analyze_digits_evenodd,
    analyze_digits_over,
    check_entry_conditions_evenodd,
    check_entry_conditions_over,
    calculate_next_stake,
    calculate_health_evenodd,
    calculate_health_over,
    EVEN_STRATEGIES,
    OVER_STRATEGIES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

CONFIG_PATH = ROOT / "config" / "bots.json"
JOURNAL_PATH = ROOT / "data" / "journal.jsonl"
CONFIG_POLL_INTERVAL = 2        # seconds between config file checks
ANALYSIS_HOLD = 30              # ticks to collect before allowing first trade
MAX_HISTORY = 200               # ticks to keep in memory


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(bot_id: str) -> dict:
    """Load config for this bot_id from bots.json, with safe defaults."""
    defaults = {
        "active": False,
        "token": "",
        "market": "R_100",
        "mode": "evenodd",          # "evenodd" | "over"
        "tradeMode": "even",         # "even" | "odd"  (evenodd only)
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
        "appId": "1089",
    }
    try:
        raw = json.loads(CONFIG_PATH.read_text())
        cfg = raw.get(bot_id, {})
        return {**defaults, **cfg}
    except Exception:
        return defaults


def write_status(bot_id: str, patch: dict):
    """Merge status fields back into bots.json for server.py to serve."""
    try:
        raw = {}
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text())
        if bot_id not in raw:
            raw[bot_id] = {}
        raw[bot_id].update(patch)
        CONFIG_PATH.write_text(json.dumps(raw, indent=2))
    except Exception as e:
        logging.warning(f"write_status failed: {e}")


def append_journal(entry: dict):
    """Append a trade record to the shared journal."""
    try:
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logging.warning(f"journal append failed: {e}")


# ---------------------------------------------------------------------------
# Contract type logic — mirrors JS executeBoltPurchase exactly
# ---------------------------------------------------------------------------

def resolve_contract(mode: str, trade_mode: str, strategy_id: int,
                     in_recovery: bool, martingale_step: int,
                     cfg: dict, history: list[int]) -> tuple[str, int]:
    """
    Returns (contract_type, barrier_value).
    Mirrors the JS executeBoltPurchase logic 1-to-1.
    """
    barrier = 0

    if mode == "evenodd":
        is_odd_mode = trade_mode == "odd"
        contract_type = "DIGITODD" if is_odd_mode else "DIGITEVEN"
        # Strategy 9 (EVEN AVALANCHE / ODD STARVATION) swaps direction
        if strategy_id == 9:
            contract_type = "DIGITEVEN" if is_odd_mode else "DIGITODD"

        if in_recovery:
            contract_type = "DIGITODD" if is_odd_mode else "DIGITEVEN"
            if cfg.get("smartRecovery"):
                recent = history[-20:]
                odd_pct = sum(1 for d in recent if d % 2 != 0) / max(len(recent), 1)
                contract_type = "DIGITODD" if odd_pct < 0.5 else "DIGITEVEN"
            elif cfg.get("alternatingRecovery"):
                step = martingale_step
                if step >= 2 and (step - 2) % 3 == 0:
                    contract_type = "DIGITEVEN" if is_odd_mode else "DIGITODD"
            elif cfg.get("alternatingRecoveryB"):
                step = martingale_step
                if step == 1 or (step > 1 and (step - 1) % 3 == 0):
                    contract_type = "DIGITEVEN" if is_odd_mode else "DIGITODD"

    else:  # over/under
        contract_type = "DIGITOVER"
        barrier = cfg.get("overBarrier", 3)
        # Strategy 9 (AVALANCHE DROP) flips to UNDER
        if strategy_id == 9:
            contract_type = "DIGITUNDER"
            barrier = cfg.get("overBarrier", 3)

        if in_recovery:
            barrier = cfg.get("recoveryBarrier", 5)
            if cfg.get("smartRecovery"):
                recent = history[-25:]
                over_rate = sum(1 for d in recent if d > 4) / max(len(recent), 1)
                if over_rate >= 0.6:
                    contract_type = "DIGITUNDER"
                    barrier = 9 - cfg.get("recoveryBarrier", 5)
            elif cfg.get("alternatingRecovery"):
                step = martingale_step
                if step >= 2 and (step - 2) % 3 == 0:
                    contract_type = "DIGITUNDER"
                    barrier = 9 - cfg.get("recoveryBarrier", 5)
            elif cfg.get("alternatingRecoveryB"):
                step = martingale_step
                if step == 1 or (step > 1 and (step - 1) % 3 == 0):
                    contract_type = "DIGITUNDER"
                    barrier = 9 - cfg.get("recoveryBarrier", 5)

    return contract_type, barrier


# ---------------------------------------------------------------------------
# Entry condition checker — wraps engine_core logic + strategy dispatch
# ---------------------------------------------------------------------------

def should_enter(history: list[int], cfg: dict, mode: str,
                 trade_mode: str) -> tuple[bool, str]:
    """
    Returns (can_enter, reason).
    Tries strategy-specific checkEntry first (strategies 2-9),
    then falls back to Normal-Mode conditions (strategy 1).
    """
    strategy_id = cfg.get("strategy", 1)
    tick_period = cfg.get("tickPeriod", 50)
    min_streak = cfg.get("minStreak", 2)
    min_signal = cfg.get("minSignal", 80)

    if mode == "evenodd":
        strategy_map = EVEN_STRATEGIES
        analyze_fn = analyze_digits_evenodd
    else:
        strategy_map = OVER_STRATEGIES
        analyze_fn = analyze_digits_over

    strategy = strategy_map.get(strategy_id, strategy_map[1])
    analysis = analyze_fn(history, tick_period)

    if strategy_id != 1 and strategy.get("check"):
        result = strategy["check"](history)
        return result["canEnter"], result["reason"]

    # Normal mode — use standard streak + probability conditions
    if mode == "evenodd":
        barrier = 0  # unused in evenodd
        result = check_entry_conditions_evenodd(history, analysis, min_streak, min_signal)
    else:
        barrier = cfg.get("overBarrier", 3)
        result = check_entry_conditions_over(history, analysis, min_streak, min_signal, barrier)

    return result["canEnter"], result.get("reason", "")


# ---------------------------------------------------------------------------
# Main bot runner
# ---------------------------------------------------------------------------

class BotRunner:
    def __init__(self, bot_id: str):
        self.bot_id = bot_id
        self.log = logging.getLogger(f"bot:{bot_id}")

        # Runtime state (reset per session)
        self.history: list[int] = []
        self.ticks_collected = 0
        self.session_profit = 0.0

        # Chain state
        self.in_chain = False
        self.current_stake = 0.35
        self.current_runs = 0
        self.martingale_step = 0
        self.in_recovery = False

        # Cooldown
        self.cooldown_until: float = 0.0

        # Live config (refreshed every CONFIG_POLL_INTERVAL ticks)
        self.cfg: dict = {}
        self.last_cfg_load = 0.0

        # DerivClient (created fresh when token changes)
        self.client: Optional[DerivClient] = None
        self._current_token: str = ""
        self._current_market: str = ""

        # Contract result event
        self._result_event = asyncio.Event()
        self._last_result: Optional[dict] = None

    # ------------------------------------------------------------------

    async def run(self):
        """Main loop — runs forever, supervised by supervisor.py."""
        self.log.info(f"Bot {self.bot_id} starting...")
        write_status(self.bot_id, {"runnerStatus": "starting", "sessionProfit": 0})

        while True:
            cfg = self._reload_config()
            token = cfg.get("token", "").strip()

            if not token:
                self.log.info("No API token configured — waiting...")
                write_status(self.bot_id, {"runnerStatus": "no_token"})
                await asyncio.sleep(5)
                continue

            # Connect (or reconnect if token or appId changed)
            app_id = str(cfg.get("appId", "1089"))
            if (token != self._current_token 
                    or app_id != getattr(self, "_current_app_id", None) 
                    or self.client is None):
                if self.client:
                    await self.client.disconnect()
                self.log.info("Connecting to Deriv...")
                write_status(self.bot_id, {"runnerStatus": "connecting"})
                self.client = DerivClient(
                    token=token,
                    app_id=str(cfg.get("appId", "1089")),
                    on_tick=self._on_tick,
                    on_contract_result=self._on_contract_result,
                    on_balance=self._on_balance,
                    on_error=self._on_error,
                    on_connected=self._on_connected,
                    on_disconnected=self._on_disconnected,
                )
                try:
                    await self.client.connect()
                    self._current_token = token
                    self._current_app_id = app_id
                except asyncio.TimeoutError:
                    self.log.error("Connection timed out — retrying in 5s")
                    await asyncio.sleep(5)
                    continue
                except Exception as e:
                    self.log.error(f"Connection failed: {e} — retrying in 5s")
                    await asyncio.sleep(5)
                    continue

            # Subscribe to market
            market = cfg.get("market", "R_100")
            if market != self._current_market:
                await self.client.subscribe_ticks(market)
                self._current_market = market
                self.history = []
                self.ticks_collected = 0
                self.log.info(f"Subscribed to {market}")

            # Keep-alive — the real work happens in _on_tick callbacks
            # Just stay alive and re-check config regularly
            await asyncio.sleep(CONFIG_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Deriv callbacks
    # ------------------------------------------------------------------

    def _reload_config(self) -> dict:
        now = time.time()
        if now - self.last_cfg_load > CONFIG_POLL_INTERVAL:
            self.cfg = load_config(self.bot_id)
            self.last_cfg_load = now
        return self.cfg

    async def _on_connected(self):
        self.log.info("Connected & authenticated")
        write_status(self.bot_id, {
            "runnerStatus": "connected",
            "balance": self.client.balance,
            "accountId": self.client.account_id,
        })

    async def _on_disconnected(self):
        self.log.warning("Disconnected — will auto-reconnect")
        write_status(self.bot_id, {"runnerStatus": "disconnected"})

    async def _on_balance(self, balance: float):
        write_status(self.bot_id, {"balance": round(balance, 2)})

    async def _on_error(self, msg: str):
        self.log.error(f"API error: {msg}")
        write_status(self.bot_id, {"lastError": msg})

    async def _on_tick(self, symbol: str, digit: int, quote: str):
        cfg = self._reload_config()

        # Ignore if wrong market
        if symbol != cfg.get("market", "R_100"):
            return

        # Accumulate history
        self.history.append(digit)
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]
        self.ticks_collected += 1

        write_status(self.bot_id, {
            "lastDigit": digit,
            "lastQuote": quote,
            "historyLen": len(self.history),
        })

        # Still warming up
        if self.ticks_collected < ANALYSIS_HOLD:
            return

        # Cooldown active
        if time.time() < self.cooldown_until:
            remaining = round(self.cooldown_until - time.time())
            write_status(self.bot_id, {"phase": "cooldown", "cooldownRemaining": remaining})
            return

        # Already in a chain execution
        if self.in_chain:
            return

        # Bot not active
        if not cfg.get("active", False):
            write_status(self.bot_id, {"phase": "idle"})
            return

        # TP/SL guard (session-level)
        tp = cfg.get("takeProfit", 15.0)
        sl = cfg.get("stopLoss", 10.0)
        if self.session_profit >= tp:
            self.log.info(f"TAKE PROFIT reached ({self.session_profit:.2f}) — pausing")
            self._pause_bot(cfg, reason="take_profit")
            return
        if self.session_profit <= -sl:
            self.log.info(f"STOP LOSS reached ({self.session_profit:.2f}) — pausing")
            self._pause_bot(cfg, reason="stop_loss")
            return

        # Check market health (don't trade POOR)
        mode = cfg.get("mode", "evenodd")
        health_fn = calculate_health_evenodd if mode == "evenodd" else calculate_health_over
        short_hist = self.history[-50:]
        long_hist = self.history[-100:]
        health = health_fn(short_hist, long_hist)
        if health and health.get("longTerm") == "POOR":
            write_status(self.bot_id, {"phase": "waiting", "waitReason": "Market health POOR"})
            return

        # Entry check
        trade_mode = cfg.get("tradeMode", "even")
        can_enter, reason = should_enter(self.history, cfg, mode, trade_mode)

        write_status(self.bot_id, {
            "phase": "waiting" if not can_enter else "firing",
            "waitReason": reason,
        })

        if can_enter:
            asyncio.create_task(self._execute_chain(cfg.copy()))

    async def _on_contract_result(self, poc: dict):
        """Called by DerivClient when a sold contract arrives."""
        self._last_result = poc
        self._result_event.set()

    # ------------------------------------------------------------------
    # Chain execution
    # ------------------------------------------------------------------

    async def _execute_chain(self, cfg: dict):
        """
        Full chain: buy → await result → martingale → loop.
        Mirrors JS startChainExecution → executeBoltPurchase → handleContractResult → _chainAfterResult.
        """
        if self.in_chain:
            return
        self.in_chain = True
        self.current_runs = 0
        self.martingale_step = 0
        self.in_recovery = False
        self.current_stake = cfg.get("stake", 0.35)

        self.log.info(f"Chain started — strategy {cfg.get('strategy',1)} stake={self.current_stake}")
        write_status(self.bot_id, {"phase": "firing"})

        target_runs = cfg.get("targetRuns", 4)
        mode = cfg.get("mode", "evenodd")
        trade_mode = cfg.get("tradeMode", "even")

        while True:
            # Safety: re-read active flag mid-chain
            live_cfg = load_config(self.bot_id)
            if not live_cfg.get("active", False):
                self.log.info("Bot deactivated mid-chain — aborting")
                break

            # TP/SL mid-chain
            if self.session_profit >= live_cfg.get("takeProfit", cfg.get("takeProfit", 15.0)):
                self.log.info("TP reached mid-chain")
                self._pause_bot(live_cfg, reason="take_profit")
                break
            if self.session_profit <= -live_cfg.get("stopLoss", cfg.get("stopLoss", 10.0)):
                self.log.info("SL reached mid-chain")
                self._pause_bot(live_cfg, reason="stop_loss")
                break

            # Target runs reached
            if self.current_runs >= target_runs:
                self.log.info(f"Target {target_runs} runs complete")
                break

            # Martingale stop
            if (cfg.get("martingaleEnabled") and
                    self.martingale_step >= cfg.get("martingaleStopStep", 3) and
                    cfg.get("martingaleStopStep", 3) != 999):
                self.log.warning(f"Martingale stop at step {self.martingale_step}")
                break

            # Resolve contract type
            contract_type, barrier = resolve_contract(
                mode, trade_mode, cfg.get("strategy", 1),
                self.in_recovery, self.martingale_step,
                cfg, self.history,
            )

            # Place trade
            stake = round(self.current_stake, 2)
            self.log.info(f"BUY {contract_type} barrier={barrier} stake={stake} step={self.martingale_step}")
            write_status(self.bot_id, {
                "phase": "firing",
                "currentStake": stake,
                "martingaleStep": self.martingale_step,
                "contractType": contract_type,
            })

            self._result_event.clear()
            self._last_result = None

            try:
                buy_resp = await self.client.buy(
                    symbol=cfg.get("market", "R_100"),
                    contract_type=contract_type,
                    stake=stake,
                    barrier=barrier if barrier != 0 else None,
                )
                contract_id = buy_resp.get("contract_id")
                self.log.info(f"Contract placed: {contract_id}")
            except Exception as e:
                self.log.error(f"Buy failed: {e}")
                break

            # Wait for result (max 30s)
            try:
                await asyncio.wait_for(self._result_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                self.log.warning("Result timeout — breaking chain")
                break

            result = self._last_result
            if not result:
                break

            # Process result
            win = result.get("status") == "won"
            profit = float(result.get("profit", 0))
            exit_digit_raw = str(result.get("exit_tick_display_value", "0"))
            exit_digit = int(exit_digit_raw[-1]) if exit_digit_raw else 0
            balance_after = float(result.get("balance_after", self.client.balance))

            self.session_profit += profit
            self.log.info(
                f"Result: {'WIN' if win else 'LOSS'} exit={exit_digit} "
                f"profit={profit:+.2f} session={self.session_profit:+.2f}"
            )

            # Journal
            entry = {
                "id": f"{int(time.time()*1000)}{random.randint(100,999)}",
                "ts": int(time.time() * 1000),
                "time": time.strftime("%H:%M:%S"),
                "botId": self.bot_id,
                "type": "win" if win else "loss",
                "market": cfg.get("market", "R_100"),
                "contractType": contract_type,
                "barrier": barrier,
                "exitDigit": exit_digit,
                "stake": stake,
                "profit": round(profit, 2),
                "balance": round(balance_after, 2),
                "sessionProfit": round(self.session_profit, 2),
                "strategy": cfg.get("strategy", 1),
                "martingaleStep": self.martingale_step,
            }
            append_journal(entry)
            write_status(self.bot_id, {
                "sessionProfit": round(self.session_profit, 2),
                "balance": round(balance_after, 2),
                "lastTrade": entry,
            })

            if win:
                self.current_runs += 1
                self.martingale_step = 0
                self.in_recovery = False
                self.current_stake = cfg.get("stake", 0.35)
            else:
                next_step = self.martingale_step + 1
                self.martingale_step = next_step
                self.in_recovery = True
                if cfg.get("martingaleEnabled", False):
                    if (next_step >= cfg.get("martingaleStopStep", 3) and
                            cfg.get("martingaleStopStep", 3) != 999):
                        self.log.warning(f"Martingale maxed at step {next_step}")
                        # will break on next iteration
                    else:
                        calc = calculate_next_stake(
                            self.current_stake,
                            cfg.get("martingaleMultiplier", 2.1),
                            next_step,
                            cfg.get("martingaleStopStep", 3),
                        )
                        self.current_stake = calc["stake"]
                else:
                    # No martingale — break chain on first loss
                    self.in_recovery = False
                    break

        # Chain complete → cooldown
        self.in_chain = False
        cooldown_secs = random.randint(8, 25)
        self.cooldown_until = time.time() + cooldown_secs
        self.log.info(f"Chain ended — cooling down {cooldown_secs}s")
        write_status(self.bot_id, {
            "phase": "cooldown",
            "cooldownRemaining": cooldown_secs,
        })

    def _pause_bot(self, cfg: dict, reason: str):
        """Flip active=False in config file (TP/SL enforcement)."""
        self.log.info(f"Pausing bot: {reason}")
        try:
            raw = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
            if self.bot_id not in raw:
                raw[self.bot_id] = {}
            raw[self.bot_id]["active"] = False
            CONFIG_PATH.write_text(json.dumps(raw, indent=2))
        except Exception as e:
            self.log.warning(f"Could not write pause: {e}")
        write_status(self.bot_id, {
            "phase": reason,
            "active": False,
        })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", required=True,
                        choices=["even", "odd", "nexus", "differ"],
                        help="Which bot to run")
    args = parser.parse_args()

    # Ensure config and data dirs exist
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text("{}")

    runner = BotRunner(args.bot)
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
