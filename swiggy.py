"""
swiggy.py — Swiggy OTP + profile completion

Steps per account:
  1. send_otp   — GET sms_otp with random device from pool
  2. verify_otp — POST login/verify {"otp": code}
  3. complete_profile — GET profile → if name missing → POST update with
                        random Indian name + generated email
  4. add_address — POST a random Delhi location
"""

import base64
import json
import logging
import random
import re
import secrets
import string
from typing import Any, Dict, Optional

import aiohttp

from config import (
    ADDRESS_ENDPOINT, DEVICE_POOL, DELHI_LOCATIONS,
    EMAIL_DOMAINS, FIRST_NAMES, LAST_NAMES,
    PROFILE_BASE_URL, OTP_ENDPOINT, PROFILE_ENDPOINT,
    UPDATE_ENDPOINT, VERIFY_ENDPOINT,
)

log = logging.getLogger("swiggy")


# ── Random identity ───────────────────────────────────────────────────────────

def random_name() -> tuple[str, str]:
    return random.choice(FIRST_NAMES), random.choice(LAST_NAMES)


def random_email(first: str, last: str) -> str:
    domain = random.choice(EMAIL_DOMAINS)
    suffix = random.randint(10, 9999)
    sep    = random.choice([".", "_", ""])
    return f"{first.lower()}{sep}{last.lower()}{suffix}@{domain}"


def random_device() -> dict:
    return random.choice(DEVICE_POOL)


def random_delhi() -> dict:
    base = random.choice(DELHI_LOCATIONS).copy()
    # jitter ±0.005 degrees (~500m) so each account looks slightly different
    base["lat"] = round(base["lat"] + random.uniform(-0.005, 0.005), 6)
    base["lng"] = round(base["lng"] + random.uniform(-0.005, 0.005), 6)
    return base


def _make_device_id() -> str:
    return secrets.token_hex(8)


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _decode_tid(tid: str) -> dict:
    try:
        parts = tid.split(".")
        if len(parts) < 2:
            return {}
        pad = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(pad).decode())
    except Exception:
        return {}


def _userid_from_tid(tid: str) -> Optional[str]:
    uid = _decode_tid(tid).get("user_id")
    return str(uid) if uid else None


# ── Headers ───────────────────────────────────────────────────────────────────

def _headers(device: dict, device_id: str, session: dict = None, extra: dict = None) -> dict:
    h = {
        "user-agent":   "Swiggy-Android",
        "content-type": "application/json; charset=utf-8",
        "accept":       "application/json; charset=utf-8",
        "version-code": device["version-code"],
        "app-version":  device["app-version"],
        "manufacturer": device["manufacturer"],
        "model-name":   device["model-name"],
        "swuid":        device_id,
        "deviceid":     device_id,
    }
    if session:
        if session.get("tid"):
            h["tid"] = session["tid"]
        if session.get("sid"):
            h["sid"] = str(session["sid"])
    if extra:
        h.update(extra)
    return h


# ── Proxy error detection ─────────────────────────────────────────────────────

def _is_proxy_err(status: int, raw: str) -> bool:
    return status in (407, 503) or "proxy" in raw.lower()


def _kwargs(proxy: Optional[str]) -> dict:
    k: dict = {"ssl": False, "timeout": aiohttp.ClientTimeout(total=15)}
    if proxy:
        k["proxy"] = proxy
    return k


# ── 1. Send OTP ───────────────────────────────────────────────────────────────

async def send_otp(phone: str, proxy: Optional[str] = None) -> Dict[str, Any]:
    device    = random_device()
    device_id = _make_device_id()
    h         = _headers(device, device_id)

    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            async with http.get(
                PROFILE_BASE_URL + OTP_ENDPOINT,
                params={"mobile": phone},
                headers=h,
                **_kwargs(proxy),
            ) as resp:
                raw  = await resp.text()
                log.info(f"send_otp {phone}: HTTP {resp.status} | {raw[:200]}")
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}

                if resp.status == 200 and data.get("statusCode") == 0:
                    tid = data.get("tid")
                    sid = data.get("sid")
                    if not sid and tid:
                        sid = _decode_tid(tid).get("sid")
                    return {
                        "ok":       True,
                        "tid":      tid,
                        "sid":      sid,
                        "deviceId": str(data.get("deviceId") or device_id),
                        "device":   device,
                        "phone":    phone,
                    }

                return {
                    "ok":          False,
                    "msg":         data.get("statusMessage") or f"HTTP {resp.status}: {raw[:120]}",
                    "proxy_error": _is_proxy_err(resp.status, raw),
                }

    except (aiohttp.ClientProxyConnectionError,
            aiohttp.ServerConnectionError,
            aiohttp.ClientConnectorError) as e:
        return {"ok": False, "msg": str(e), "proxy_error": True}
    except Exception as e:
        log.error(f"send_otp {phone}: {e}")
        return {"ok": False, "msg": str(e), "proxy_error": False}


# ── 2. Verify OTP ─────────────────────────────────────────────────────────────

async def verify_otp(pending: Dict[str, Any], otp: str,
                     proxy: Optional[str] = None) -> Dict[str, Any]:
    device    = pending.get("device") or random_device()
    device_id = pending["deviceId"]
    h         = _headers(device, device_id, extra={
        **({"tid": pending["tid"]} if pending.get("tid") else {}),
        **({"sid": str(pending["sid"])} if pending.get("sid") else {}),
    })

    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            async with http.post(
                PROFILE_BASE_URL + VERIFY_ENDPOINT,
                params={"otp_source": "Sms-manual"},
                json={"otp": otp},
                headers=h,
                **_kwargs(proxy),
            ) as resp:
                raw  = await resp.text()
                log.info(f"verify_otp {pending['phone']}: HTTP {resp.status} | {raw[:200]}")
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}

                if resp.status != 200 or data.get("statusCode") != 0:
                    return {
                        "ok":          False,
                        "msg":         data.get("statusMessage") or f"HTTP {resp.status}: {raw[:120]}",
                        "proxy_error": _is_proxy_err(resp.status, raw),
                    }

                session: Dict[str, Any] = {}
                inner = data.get("data") if isinstance(data.get("data"), dict) else {}
                for key in ("token", "tid", "sid"):
                    if inner.get(key):
                        session[key] = inner[key]
                    elif data.get(key):
                        session[key] = data[key]

                session["deviceId"] = str(data.get("deviceId") or device_id)
                session["device"]   = device

                uid = _userid_from_tid(session.get("tid", ""))
                if not uid or uid == "0":
                    uid = str(
                        data.get("userId") or data.get("userid") or
                        inner.get("userId") or inner.get("userid") or ""
                    ).strip() or None
                if not uid:
                    return {"ok": False, "msg": f"No user_id: {raw[:200]}"}

                session["userid"]     = uid
                session["customerId"] = uid
                session["mobile"]     = str(inner.get("mobile") or pending.get("phone", ""))
                return {"ok": True, "session": session}

    except (aiohttp.ClientProxyConnectionError,
            aiohttp.ServerConnectionError,
            aiohttp.ClientConnectorError) as e:
        return {"ok": False, "msg": str(e), "proxy_error": True}
    except Exception as e:
        log.error(f"verify_otp {pending['phone']}: {e}")
        return {"ok": False, "msg": str(e), "proxy_error": False}


# ── 3. Complete profile (name + email) ────────────────────────────────────────

async def complete_profile(session: Dict[str, Any],
                           proxy: Optional[str] = None) -> Dict[str, Any]:
    """
    GET profile → check if name is set.
    If not (new account) → POST update with random name + generated email.
    Returns updated session with name/email fields added.
    """
    device    = session.get("device") or random_device()
    device_id = session["deviceId"]
    h         = _headers(device, device_id, session=session)

    # GET profile
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            async with http.get(
                PROFILE_BASE_URL + PROFILE_ENDPOINT,
                headers=h,
                **_kwargs(proxy),
            ) as resp:
                raw  = await resp.text()
                log.info(f"get_profile {session['mobile']}: HTTP {resp.status} | {raw[:200]}")
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}

                if resp.status != 200:
                    return {"ok": False, "msg": f"profile GET HTTP {resp.status}",
                            "proxy_error": _is_proxy_err(resp.status, raw)}

                inner = data.get("data") or {}
                if isinstance(inner, dict):
                    existing_name  = str(inner.get("name") or inner.get("full_name") or "").strip()
                    existing_email = str(inner.get("email") or "").strip()
                else:
                    existing_name  = ""
                    existing_email = ""

    except (aiohttp.ClientProxyConnectionError,
            aiohttp.ServerConnectionError,
            aiohttp.ClientConnectorError) as e:
        return {"ok": False, "msg": str(e), "proxy_error": True}
    except Exception as e:
        log.warning(f"get_profile {session['mobile']}: {e}")
        # non-fatal — continue with update anyway
        existing_name  = ""
        existing_email = ""

    # generate identity
    first, last = random_name()
    full_name   = f"{first} {last}"
    email       = existing_email or random_email(first, last)

    # only update if name is blank (new account)
    if not existing_name:
        payload = {"name": full_name, "email": email}
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
                async with http.post(
                    PROFILE_BASE_URL + UPDATE_ENDPOINT,
                    json=payload,
                    headers=h,
                    **_kwargs(proxy),
                ) as resp:
                    raw  = await resp.text()
                    log.info(f"update_profile {session['mobile']}: HTTP {resp.status} | {raw[:200]}")
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = {}
                    if resp.status not in (200, 201):
                        log.warning(f"profile update failed: {raw[:150]}")
                        # non-fatal — still save session
        except (aiohttp.ClientProxyConnectionError,
                aiohttp.ServerConnectionError,
                aiohttp.ClientConnectorError) as e:
            return {"ok": False, "msg": str(e), "proxy_error": True}
        except Exception as e:
            log.warning(f"update_profile {session['mobile']}: {e}")
    else:
        full_name = existing_name

    session["name"]  = full_name
    session["email"] = email
    return {"ok": True, "session": session}


# ── 4. Add Delhi address ──────────────────────────────────────────────────────

async def add_address(session: Dict[str, Any],
                      proxy: Optional[str] = None) -> bool:
    """
    POST a random Delhi delivery address.
    Returns True if saved (or non-fatal failure — we don't skip the account for this).
    """
    device    = session.get("device") or random_device()
    device_id = session["deviceId"]
    h         = _headers(device, device_id, session=session)
    loc       = random_delhi()

    payload = {
        "annotation":    "Home",
        "flatNo":        f"{random.randint(1,999)}",
        "landmark":      "",
        "lat":           loc["lat"],
        "lng":           loc["lng"],
        "locality":      loc["area"],
        "city":          loc["city"],
        "area":          loc["area"],
        "deliveryArea":  loc["area"],
        "addressLine1":  f"Flat {random.randint(1,999)}, {loc['area']}",
        "addressLine2":  loc["city"],
        "pincode":       str(random.randint(110001, 110096)),
    }

    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            async with http.post(
                PROFILE_BASE_URL + ADDRESS_ENDPOINT,
                json=payload,
                headers=h,
                **_kwargs(proxy),
            ) as resp:
                raw = await resp.text()
                log.info(f"add_address {session['mobile']}: HTTP {resp.status} | {raw[:150]}")
                return resp.status in (200, 201)
    except Exception as e:
        log.warning(f"add_address {session['mobile']}: {e}")
        return False
