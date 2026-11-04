"""
proxy.py — Proxy pool

Priority:
  1. HOST_PROXY from config (env var) — used first, always
  2. Admin-added proxies via /proxy command

On ANY single failure → proxy marked dead immediately.
If pool empty → bot asks admin, workers wait up to 5 min.
Admin sends /proxy → event fires → workers resume.

Formats: http://user:pass@host:port  |  host:port:user:pass
         http://host:port            |  socks5://user:pass@host:port
"""

import asyncio
import logging
import re
from typing import Callable, List, Optional, Set

import aiohttp

from config import HOST_PROXY

log = logging.getLogger("proxy")

PROXY_TEST_URL     = "https://profile.swiggy.com/api/v3/app/sms_otp?mobile=9999999999"
PROXY_TEST_TIMEOUT = 8
PROXY_WAIT_TIMEOUT = 300  # 5 min


def _normalise(raw: str) -> Optional[str]:
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None
    if re.match(r'^(http|https|socks5)://', raw, re.I):
        return raw
    parts = raw.split(":")
    if len(parts) == 4:
        host, port, user, pw = parts
        return f"http://{user}:{pw}@{host}:{port}"
    if len(parts) == 2 and parts[1].isdigit():
        return f"http://{raw}"
    return None


def _hide(url: Optional[str]) -> str:
    if not url:
        return "none"
    return re.sub(r'(:)[^@]+(@)', r'\1***\2', url)


class ProxyPool:
    def __init__(self):
        self._proxies: List[str]       = []
        self._dead:    Set[str]        = set()
        self._idx      = 0
        self._lock     = asyncio.Lock()
        self._ask_cb:  Optional[Callable] = None
        self._added_ev = asyncio.Event()   # fires when new proxy added

        # pre-load host proxy
        if HOST_PROXY:
            url = _normalise(HOST_PROXY)
            if url:
                self._proxies.append(url)
                log.info(f"Host proxy loaded: {_hide(url)}")

    def set_ask_callback(self, cb: Callable):
        self._ask_cb = cb

    # ── add ───────────────────────────────────────────────────────────────────

    def add(self, raw: str) -> int:
        added = 0
        for line in raw.splitlines():
            url = _normalise(line)
            if not url:
                continue
            self._dead.discard(url)   # re-adding clears dead flag
            if url not in self._proxies:
                self._proxies.append(url)
                added += 1
                log.info(f"Proxy added: {_hide(url)}")
        if added:
            self._added_ev.set()
        return added

    # ── query ─────────────────────────────────────────────────────────────────

    def current(self) -> Optional[str]:
        alive = self._alive()
        return alive[self._idx % len(alive)] if alive else None

    def has_proxy(self) -> bool:
        return bool(self._alive())

    def status(self) -> str:
        return f"Active: {len(self._alive())}  Dead: {len(self._dead)}"

    def clear_dead(self):
        self._dead.clear()
        log.info("Dead proxies cleared")

    # ── failure / success ─────────────────────────────────────────────────────

    async def mark_failure(self, proxy: str):
        async with self._lock:
            if proxy and proxy not in self._dead:
                log.warning(f"Proxy dead: {_hide(proxy)}")
                self._dead.add(proxy)
                self._idx = 0   # reset index
            if not self._alive() and self._ask_cb:
                asyncio.create_task(self._ask_cb())

    def mark_success(self, proxy: str):
        pass   # single-strike — no recovery once dead

    # ── wait for proxy ────────────────────────────────────────────────────────

    async def wait_for_proxy(self, log_cb: Callable) -> Optional[str]:
        """
        Called when pool is empty. Sends ask to admin once,
        then waits PROXY_WAIT_TIMEOUT seconds for /proxy command.
        Returns proxy URL or None on timeout.
        """
        if self._ask_cb:
            asyncio.create_task(self._ask_cb())

        log_cb("⚠️", f"all proxies dead — waiting up to {PROXY_WAIT_TIMEOUT}s for admin...")

        deadline = asyncio.get_event_loop().time() + PROXY_WAIT_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            self._added_ev.clear()
            remaining = deadline - asyncio.get_event_loop().time()
            try:
                await asyncio.wait_for(
                    self._added_ev.wait(), timeout=min(10.0, remaining)
                )
            except asyncio.TimeoutError:
                pass
            if self._alive():
                p = self.current()
                log_cb("✅", f"proxy received: `{_hide(p)}` — resuming")
                return p

        log_cb("❌", "no proxy after 5 min — skipping this number")
        return None

    # ── internal ──────────────────────────────────────────────────────────────

    def _alive(self) -> List[str]:
        return [p for p in self._proxies if p not in self._dead]

    async def test(self, proxy_url: str) -> bool:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    PROXY_TEST_URL, proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=PROXY_TEST_TIMEOUT),
                    ssl=False,
                ) as r:
                    return r.status in (200, 403, 400)
        except Exception:
            return False


pool = ProxyPool()
