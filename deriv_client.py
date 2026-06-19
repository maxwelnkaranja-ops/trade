"""
deriv_client.py — Persistent async Deriv WebSocket client.
Handles: REST OTP auth, balance feed, tick subscriptions, contract buying,
         proposal_open_contract results, and keep-alive pings.

New API flow (replacing legacy ws.binaryws.com + authorize message):
  1. POST  /trading/v1/options/accounts/{account_id}/otp  → returns authenticated WS URL
  2. Connect to that WS URL — no authorize message needed; connection is pre-authenticated

Usage (from bot_runner or anywhere):
    client = DerivClient(token, app_id="33AsK8F3vLO1plM4tD2Wj", account_id="<id>")
    await client.connect()
    await client.subscribe_ticks("R_100", on_tick_callback)
    contract_id = await client.buy(symbol, contract_type, stake, barrier=None)
    # results arrive via on_result callback set at construction
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Optional

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("deriv_client")

REST_BASE = "https://api.derivws.com"
PING_INTERVAL = 25          # seconds
RECONNECT_DELAY = 10        # seconds (increased to avoid rate limits)
MAX_RECONNECT_DELAY = 120   # seconds (increased maximum delay)
DEFAULT_APP_ID = "33ADPhDZXR2F60qhPudXB"


class DerivClient:
    """
    One persistent WebSocket connection to Deriv (new Options API).
    Authenticates via REST OTP → WS URL; no authorize message over WS.

    Callbacks (all async-compatible, can be sync or async coroutines):
      on_tick(symbol: str, digit: int, quote: str)
      on_contract_result(result: dict)   — called when a sold contract arrives
      on_balance(balance: float)
      on_error(msg: str)
      on_connected()
      on_disconnected()
    """

    def __init__(
        self,
        token: str,
        app_id: str = DEFAULT_APP_ID,
        account_id: str = "",
        on_tick: Optional[Callable] = None,
        on_contract_result: Optional[Callable] = None,
        on_balance: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
        on_connected: Optional[Callable] = None,
        on_disconnected: Optional[Callable] = None,
    ):
        self.token = token
        self.app_id = app_id
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._authenticated = False
        self._balance: float = 0.0
        self._account_id_resolved: str = account_id  # required for OTP — fetch from accounts list if empty
        self._subscribed_markets: set[str] = set()
        self._pending_buys: dict[str, asyncio.Future] = {}   # req_id → Future[buy_data]
        self._req_counter = 0

        # Callbacks
        self.on_tick = on_tick
        self.on_contract_result = on_contract_result
        self.on_balance = on_balance
        self.on_error = on_error
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected

        self._reconnect_delay = RECONNECT_DELAY
        self._loop_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self):
        """Start the background receive-loop. Returns once the first balance update confirms auth."""
        self._running = True
        auth_event = asyncio.Event()
        self._auth_event = auth_event
        self._loop_task = asyncio.create_task(self._run_loop())
        # No timeout because rate limits can take minutes
        await auth_event.wait()

    async def disconnect(self):
        """Gracefully stop everything."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

    async def subscribe_ticks(self, symbol: str):
        """Subscribe to live ticks for a market symbol."""
        if symbol in self._subscribed_markets:
            return
        self._subscribed_markets.add(symbol)
        await self._send({"ticks": symbol, "subscribe": 1})

    async def unsubscribe_ticks(self, symbol: str):
        """Unsubscribe from a market."""
        self._subscribed_markets.discard(symbol)
        await self._send({"forget_all": "ticks"})
        # Re-subscribe any remaining markets
        for s in self._subscribed_markets:
            await self._send({"ticks": s, "subscribe": 1})

    async def buy(
        self,
        symbol: str,
        contract_type: str,
        stake: float,
        barrier: Optional[int] = None,
        duration: int = 1,
        duration_unit: str = "t",
        currency: str = "USD",
    ) -> dict:
        """
        Buy a contract. Returns the buy response dict (contains contract_id).
        Raises RuntimeError on API error.
        """
        if not self._authenticated:
            raise RuntimeError("Not authenticated — call connect() first")

        req_id = self._next_req_id()
        params: dict = {
            "amount": round(stake, 2),
            "basis": "stake",
            "contract_type": contract_type,
            "currency": currency,
            "duration": duration,
            "duration_unit": duration_unit,
            "underlying_symbol": symbol,   # new API field name (was: symbol)
        }
        if barrier is not None:
            params["barrier"] = barrier

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_buys[req_id] = fut

        await self._send({
            "buy": 1,
            "subscribe": 1,
            "price": round(stake, 2),
            "parameters": params,
            "req_id": int(req_id),
        })

        try:
            result = await asyncio.wait_for(fut, timeout=15)
            return result
        except asyncio.TimeoutError:
            self._pending_buys.pop(req_id, None)
            raise RuntimeError("Buy request timed out after 15s")

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def account_id(self) -> str:
        return self._account_id_resolved

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _next_req_id(self) -> str:
        self._req_counter += 1
        return str(self._req_counter)

    async def _send(self, msg: dict):
        if self._ws and self._ws.open:
            try:
                await self._ws.send(json.dumps(msg))
            except Exception as e:
                logger.warning(f"Send failed: {e}")

    async def _fetch_otp_ws_url(self) -> str:
        """
        Two-step REST auth:
          1. GET  /trading/v1/options/accounts          → pick account_id if not set
          2. POST /trading/v1/options/accounts/{id}/otp → returns authenticated WS URL
        """
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Deriv-App-ID": self.app_id,
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            # Step 1: resolve account_id if not already known
            if not self._account_id_resolved:
                async with session.get(
                    f"{REST_BASE}/trading/v1/options/accounts",
                    headers=headers,
                ) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 60))
                        logger.warning(f"Rate limited! Retrying after {retry_after}s...")
                        await asyncio.sleep(retry_after)
                        return await self._fetch_otp_ws_url()
                    if resp.status != 200:
                        raise RuntimeError(f"Accounts fetch failed: {resp.status}")
                    body = await resp.json()
                    accounts = body.get("data", [])
                    if not accounts:
                        raise RuntimeError("No trading accounts found for this token")
                    self._account_id_resolved = accounts[0].get("id") or accounts[0].get("account_id", "")
                    logger.info(f"Resolved account_id: {self._account_id_resolved}")

            # Step 2: get OTP WebSocket URL
            async with session.post(
                f"{REST_BASE}/trading/v1/options/accounts/{self._account_id_resolved}/otp",
                headers=headers,
            ) as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limited! Retrying after {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    return await self._fetch_otp_ws_url()
                if resp.status != 200:
                    raise RuntimeError(f"OTP fetch failed: {resp.status}")
                body = await resp.json()
                ws_url = body["data"]["url"]
                logger.info(f"Got authenticated WS URL (account {self._account_id_resolved})")
                return ws_url

    async def _run_loop(self):
        """Main reconnect + receive loop using OTP-authenticated WebSocket URLs."""
        while self._running:
            try:
                ws_url = await self._fetch_otp_ws_url()
                async with websockets.connect(ws_url, ping_interval=None) as ws:
                    self._ws = ws
                    self._reconnect_delay = RECONNECT_DELAY
                    logger.info("WS connected (pre-authenticated via OTP)")

                    # Connection is already authenticated by the OTP URL itself —
                    # there is no "authorize" request/response round-trip in the
                    # new API, so we flip the flag here rather than waiting for
                    # a message that will never arrive.
                    self._authenticated = True
                    await ws.send(json.dumps({"balance": 1, "subscribe": 1}))
                    await ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))
                    for sym in self._subscribed_markets:
                        await ws.send(json.dumps({"ticks": sym, "subscribe": 1}))

                    if self.on_connected:
                        await self._cb(self.on_connected)
                    if hasattr(self, "_auth_event"):
                        self._auth_event.set()

                    # Start ping task
                    ping_task = asyncio.create_task(self._ping_loop())
                    try:
                        async for raw in ws:
                            await self._handle_message(raw)
                    except ConnectionClosed as e:
                        logger.warning(f"WS closed: {e}")
                    finally:
                        ping_task.cancel()
                        self._authenticated = False
                        self._on_disconnected_cb()

            except Exception as e:
                logger.error(f"WS/auth error: {e}")

            if not self._running:
                break

            logger.info(f"Reconnecting in {self._reconnect_delay}s...")
            self._authenticated = False
            self._subscribed_markets_copy = set(self._subscribed_markets)
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 1.5, MAX_RECONNECT_DELAY)

    def _on_disconnected_cb(self):
        if self.on_disconnected:
            try:
                result = self.on_disconnected()
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                pass
        return True

    async def _ping_loop(self):
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await self._send({"ping": 1})

    async def _handle_message(self, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = data.get("msg_type")

        # NOTE: there is no "authorize" message in the new OTP flow — the
        # connection is pre-authenticated by the WS URL itself. Auth state,
        # feed subscriptions, and the on_connected callback are all handled
        # right after the socket opens in _run_loop().

        # ── Balance ───────────────────────────────────────────────────
        if msg_type == "balance":
            bal = data.get("balance", {})
            new_bal = float(bal.get("balance", self._balance))
            self._balance = new_bal
            if self.on_balance:
                await self._cb(self.on_balance, new_bal)

        # ── Ticks ─────────────────────────────────────────────────────
        elif msg_type == "tick":
            tick = data.get("tick", {})
            quote_str = str(tick.get("quote", "0"))
            digit = int(quote_str[-1])
            symbol = tick.get("symbol", "")
            if self.on_tick:
                await self._cb(self.on_tick, symbol, digit, quote_str)

        # ── Buy response ──────────────────────────────────────────────
        elif msg_type == "buy":
            req_id = str(data.get("req_id", ""))
            if data.get("error"):
                err_msg = data["error"]["message"]
                logger.error(f"Buy error: {err_msg}")
                if req_id in self._pending_buys:
                    self._pending_buys.pop(req_id).set_exception(RuntimeError(err_msg))
                if self.on_error:
                    await self._cb(self.on_error, f"Buy error: {err_msg}")
            else:
                buy_data = data.get("buy", {})
                if req_id in self._pending_buys:
                    self._pending_buys.pop(req_id).set_result(buy_data)

        # ── Contract result ───────────────────────────────────────────
        elif msg_type == "proposal_open_contract":
            poc = data.get("proposal_open_contract", {})
            if poc.get("is_sold"):
                # Update balance from contract balance_after
                bal_after = poc.get("balance_after")
                if bal_after is not None:
                    self._balance = float(bal_after)
                    if self.on_balance:
                        await self._cb(self.on_balance, self._balance)
                if self.on_contract_result:
                    await self._cb(self.on_contract_result, poc)

        # ── Errors ────────────────────────────────────────────────────
        elif data.get("error"):
            err = data["error"]["message"]
            logger.warning(f"API error ({msg_type}): {err}")
            if self.on_error:
                await self._cb(self.on_error, err)

    @staticmethod
    async def _cb(fn, *args):
        """Call sync or async callback safely."""
        try:
            result = fn(*args)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"Callback error: {e}")
