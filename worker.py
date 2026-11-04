"""
worker.py — Per-phone Swiggy account creation

Flow per phone:
  1. snapshot_sms() → baseline_keys (Set[str]), base_url
  2. send_otp via proxy → record request_epoch
  3. wait_for_otp polls Firebase every 1s, matches new keys OR fresh timestamp
     → timeout → resnapshot + resend once
  4. verify_otp → session
  5. complete_profile → random Indian name + generated email
  6. add_address → random Delhi location
  7. save JSON, return session

Proxy failure at any step:
  → mark dead immediately
  → pool empty → ask admin, pause worker up to 5 min
  → retry with new proxy
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set

from config import MAX_CONCURRENT, OTP_TIMEOUT, SESSIONS_DIR
from firebase import snapshot_sms, wait_for_otp
from proxy import pool as proxy_pool
from swiggy import add_address, complete_profile, send_otp, verify_otp

log = logging.getLogger("worker")

os.makedirs(SESSIONS_DIR, exist_ok=True)


# ── Session save ──────────────────────────────────────────────────────────────

def save_session(session: Dict[str, Any]) -> str:
    phone = session.get("mobile") or session.get("userid", "unknown")
    path  = os.path.join(SESSIONS_DIR, f"swiggy_{phone}.json")
    tmp   = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    log.info(f"Session saved: {path}")
    return path


# ── Proxy-aware wrapper ───────────────────────────────────────────────────────

async def _with_proxy(coro_fn: Callable, log_cb: Callable,
                      max_attempts: int = 2) -> Optional[Any]:
    """
    Call coro_fn(proxy) up to max_attempts times.
    proxy_error=True  → mark dead, wait for new proxy, retry once.
    proxy_error=False → Swiggy-side error, give up immediately.
    """
    for attempt in range(max_attempts):
        proxy = proxy_pool.current()

        if proxy is None and attempt == 0:
            proxy = await proxy_pool.wait_for_proxy(log_cb)
            if proxy is None:
                return None

        result = await coro_fn(proxy)

        if result.get("ok"):
            return result

        if result.get("proxy_error"):
            if proxy:
                await proxy_pool.mark_failure(proxy)
            if attempt < max_attempts - 1:
                log_cb("🔄", "proxy dead — waiting for new proxy...")
                new_proxy = await proxy_pool.wait_for_proxy(log_cb)
                if new_proxy is None:
                    return None
                # loop with new proxy
            else:
                log_cb("❌", "proxy failed again after replacement — skipping")
                return None
        else:
            log_cb("❌", f"`{result.get('msg', 'unknown error')}`")
            return None

    return None


# ── Main per-phone flow ───────────────────────────────────────────────────────

async def process_phone(
    phone:        str,
    device_id:    str,
    firebase_url: str,
    log_cb:       Callable,
) -> Optional[Dict[str, Any]]:

    # ── 1. snapshot Firebase SMS state ────────────────────────────────────────
    log_cb("📡", "snapshotting Firebase SMS state...")
    baseline_keys, base_url = await snapshot_sms(firebase_url, device_id)
    log_cb("📡", f"snapshot: {len(baseline_keys)} existing SMS keys")

    # ── 2. send OTP ───────────────────────────────────────────────────────────
    log_cb("📤", "sending OTP to Swiggy...")
    request_epoch = time.monotonic()
    pending = await _with_proxy(
        lambda px: send_otp(phone, proxy=px), log_cb
    )
    if not pending:
        return None
    log_cb("✅", f"OTP sent — tid=`{str(pending.get('tid', ''))[:24]}`")

    # ── 3. wait for OTP from Firebase (50s × 2 attempts) ─────────────────────
    otp      = None
    attempts = 0

    while attempts < 2 and otp is None:
        log_cb("⏳", f"watching Firebase for OTP (attempt {attempts + 1}/2, {OTP_TIMEOUT}s)...")
        otp = await wait_for_otp(
            firebase_url  = firebase_url,
            device_id     = device_id,
            baseline_keys = baseline_keys,
            request_epoch = request_epoch,
            timeout       = OTP_TIMEOUT,
        )
        if otp:
            break

        attempts += 1
        if attempts >= 2:
            break

        log_cb("🔄", "OTP timeout — re-snapshotting and resending OTP...")
        baseline_keys, base_url = await snapshot_sms(firebase_url, device_id)
        request_epoch = time.monotonic()

        pending2 = await _with_proxy(
            lambda px: send_otp(phone, proxy=px), log_cb
        )
        if pending2:
            pending = pending2
            log_cb("📤", "OTP re-sent — watching again...")
        else:
            log_cb("❌", "retry OTP send failed")
            break

    if otp is None:
        log_cb("⏰", f"OTP timeout after {OTP_TIMEOUT * 2}s — skipping")
        return None

    log_cb("🔑", f"OTP caught: `{otp}`")

    # ── 4. verify OTP ─────────────────────────────────────────────────────────
    log_cb("🔐", "verifying OTP with Swiggy...")
    verify_res = await _with_proxy(
        lambda px: verify_otp(pending, otp, proxy=px), log_cb
    )
    if not verify_res:
        return None

    session = verify_res["session"]
    log_cb("✅", f"login OK — user: `{session.get('userid', '?')}`")

    # ── 5. complete profile (name + email) ────────────────────────────────────
    log_cb("👤", "setting profile name and email...")
    profile_res = await _with_proxy(
        lambda px: complete_profile(session, proxy=px), log_cb
    )
    if profile_res and profile_res.get("ok"):
        session = profile_res["session"]
        log_cb("👤", f"`{session.get('name', '?')}` / `{session.get('email', '?')}`")
    else:
        log_cb("⚠️", "profile update failed — continuing anyway")

    # ── 6. add Delhi address ──────────────────────────────────────────────────
    log_cb("📍", "adding random Delhi address...")
    addr_ok = await add_address(session, proxy=proxy_pool.current())
    log_cb("📍", "address saved" if addr_ok else "address save failed — continuing")

    # ── 7. save ───────────────────────────────────────────────────────────────
    save_session(session)
    log_cb(
        "✅",
        f"account created ✓ | ID: `{session.get('customerId', '?')}` "
        f"| `{session.get('name', '')}` | `{session.get('mobile', '')}`"
    )
    return session


# ── Batch runner ──────────────────────────────────────────────────────────────

async def run_all(
    devices:      List[Dict[str, str]],
    firebase_url: str,
    status_cb:    Callable,
) -> List[Dict[str, Any]]:
    sem        = asyncio.Semaphore(MAX_CONCURRENT)
    results:   List[Dict] = []
    log_lines: List[str]  = []
    done       = 0
    failed     = 0
    lock       = asyncio.Lock()

    async def _run_one(entry: Dict):
        nonlocal done, failed
        phone     = entry["phone"]
        device_id = entry["device_id"]

        def log_cb(icon: str, msg: str):
            line = f"{icon} `{phone}` — {msg}"
            log_lines.append(line)
            asyncio.create_task(
                status_cb(done, failed, len(devices), list(log_lines[-20:]))
            )

        async with sem:
            session = await process_phone(phone, device_id, firebase_url, log_cb)
            async with lock:
                if session:
                    results.append(session)
                    done   += 1
                else:
                    failed += 1
            await status_cb(done, failed, len(devices), list(log_lines[-20:]))

    await asyncio.gather(*[_run_one(e) for e in devices])
    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def all_sessions() -> List[Dict]:
    out = []
    for fn in os.listdir(SESSIONS_DIR):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(SESSIONS_DIR, fn), encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                pass
    return out


def clear_sessions() -> int:
    count = 0
    for fn in os.listdir(SESSIONS_DIR):
        if fn.endswith(".json"):
            try:
                os.remove(os.path.join(SESSIONS_DIR, fn))
                count += 1
            except Exception:
                pass
    return count
