"""
bot.py — Telegram bot entry point

Commands / buttons:
  /start          — main menu
  /proxy <lines>  — add proxies
  /clearproxy     — reset dead proxy list
  /status         — proxy + session stats

Flow:
  User sends Firebase URL
  → bot finds devices + phones
  → creates Swiggy accounts via OTP
  → sends each JSON + all_accounts.json at end

Proxy prompt:
  When proxy pool is empty the bot auto-messages admin asking for new proxies.
  Admin replies with /proxy <lines>.
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional, Set

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
)
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)
from telegram.request import HTTPXRequest

from config import ADMIN_IDS, BOT_TOKEN, SESSIONS_DIR
from firebase import get_devices
from proxy import pool as proxy_pool
from worker import all_sessions, clear_sessions, run_all

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("bot")

os.makedirs(SESSIONS_DIR, exist_ok=True)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _is_admin(uid: int) -> bool:
    return not ADMIN_IDS or uid in ADMIN_IDS


# ── Per-user state ────────────────────────────────────────────────────────────

class UserState:
    __slots__ = ("phase", "firebase_url")
    def __init__(self):
        self.phase:        str           = "idle"   # idle | waiting_url | running
        self.firebase_url: Optional[str] = None

_states: Dict[int, UserState] = {}

def _st(uid: int) -> UserState:
    if uid not in _states:
        _states[uid] = UserState()
    return _states[uid]


# ── Keyboards ─────────────────────────────────────────────────────────────────

def _main_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Add Firebase URL", callback_data="add_url")],
        [
            InlineKeyboardButton("📦 Get All JSONs",   callback_data="get_jsons"),
            InlineKeyboardButton("🗑 Clear Sessions",  callback_data="clear"),
        ],
        [
            InlineKeyboardButton("📡 Proxy Status",    callback_data="proxy_status"),
            InlineKeyboardButton("➕ Add Proxy",       callback_data="add_proxy"),
        ],
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _edit(msg, text: str, **kw):
    try:
        await msg.edit_text(text, **kw)
    except Exception:
        pass


async def _send_sessions(uid: int, ctx: ContextTypes.DEFAULT_TYPE, sessions: List[dict]):
    """Send each session JSON + combined file."""
    if not sessions:
        return
    for s in sessions:
        path = os.path.join(SESSIONS_DIR, f"swiggy_{s.get('mobile','?')}.json")
        if os.path.exists(path):
            with open(path, "rb") as f:
                await ctx.bot.send_document(
                    uid, document=f,
                    filename=os.path.basename(path),
                    caption=f"✅ `{s.get('mobile','?')}` | ID `{s.get('customerId','?')}`",
                    parse_mode="Markdown",
                )
    # combined
    combined_path = os.path.join(SESSIONS_DIR, "all_accounts.json")
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)
    with open(combined_path, "rb") as f:
        await ctx.bot.send_document(
            uid, document=f,
            filename="all_accounts.json",
            caption=f"📦 *{len(sessions)} accounts combined*",
            parse_mode="Markdown",
        )


# ── Proxy ask callback (called by proxy pool when empty) ─────────────────────

_app_ref = None   # set in main()


async def _ask_admin_for_proxy():
    if _app_ref is None:
        return
    for uid in (ADMIN_IDS or []):
        try:
            await _app_ref.bot.send_message(
                uid,
                "⚠️ *Proxy pool is empty or all proxies are dead.*\n\n"
                "Send new proxies with:\n"
                "`/proxy http://user:pass@host:port`\n\n"
                "Or one per line for multiple proxies.",
                parse_mode="Markdown",
            )
        except Exception as e:
            log.warning(f"ask_admin_for_proxy failed: {e}")


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    await update.message.reply_text(
        "🍔 *Swiggy Firebase OTP Creator*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Send Firebase URL → finds online devices → "
        "grabs phones → hits Swiggy OTP → catches OTP from Firebase "
        "→ creates accounts → sends you all JSONs.\n\n"
        "👇 Choose:",
        parse_mode="Markdown",
        reply_markup=_main_kb(uid),
    )


async def cmd_proxy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    text = update.message.text or ""
    # strip /proxy command
    lines = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
    added = proxy_pool.add(lines)
    await update.message.reply_text(
        f"✅ Added {added} proxy/proxies.\n{proxy_pool.status()}"
    )


async def cmd_clearproxy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    proxy_pool.clear_dead()
    await update.message.reply_text("🧹 Dead proxies cleared.")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    sessions = all_sessions()
    await update.message.reply_text(
        f"📊 *Status*\n"
        f"━━━━━━━━━━━━━━\n"
        f"🔌 Proxies  : `{proxy_pool.status()}`\n"
        f"💾 Sessions : `{len(sessions)}`\n",
        parse_mode="Markdown",
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    if not _is_admin(uid):
        await q.answer("⛔ Unauthorized", show_alert=True)
        return
    await q.answer()
    data = q.data
    st   = _st(uid)

    if data == "add_url":
        st.phase = "waiting_url"
        await q.edit_message_text(
            "📡 *Send Firebase URL*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Example:\n`https://your-project-default-rtdb.firebaseio.com/`\n\n"
            "Paste the URL now 👇",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="cancel")
            ]]),
        )

    elif data == "cancel":
        st.phase = "idle"
        await q.edit_message_text("Cancelled.", reply_markup=_main_kb(uid))

    elif data == "proxy_status":
        sessions = all_sessions()
        await q.edit_message_text(
            f"📡 *Proxy & Session Status*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Proxies  : `{proxy_pool.status()}`\n"
            f"Sessions : `{len(sessions)}`\n",
            parse_mode="Markdown",
            reply_markup=_main_kb(uid),
        )

    elif data == "add_proxy":
        st.phase = "waiting_proxy"
        await q.edit_message_text(
            "➕ *Add Proxies*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Send one or more proxy lines:\n"
            "`http://user:pass@host:port`\n"
            "`host:port:user:pass`\n"
            "`socks5://host:port`\n\n"
            "One per line for multiple. Send now 👇",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="cancel")
            ]]),
        )

    elif data == "get_jsons":
        sessions = all_sessions()
        if not sessions:
            await q.answer("No sessions saved yet.", show_alert=True)
            return
        await q.edit_message_text(
            f"📦 Sending {len(sessions)} account JSONs...",
            reply_markup=None,
        )
        await _send_sessions(uid, ctx, sessions)
        await ctx.bot.send_message(uid, "✅ Done!", reply_markup=_main_kb(uid))

    elif data == "clear":
        count = clear_sessions()
        await q.answer(f"🗑 Cleared {count} session files.", show_alert=True)


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    if not _is_admin(uid):
        return
    text = (update.message.text or "").strip()
    st   = _st(uid)

    # ── proxy input ───────────────────────────────────────────────────────────
    if st.phase == "waiting_proxy":
        added = proxy_pool.add(text)
        st.phase = "idle"
        await update.message.reply_text(
            f"✅ Added {added} proxy/proxies.\n{proxy_pool.status()}",
            reply_markup=_main_kb(uid),
        )
        return

    # ── firebase URL input ────────────────────────────────────────────────────
    if st.phase != "waiting_url":
        await update.message.reply_text("Use /start to begin.", reply_markup=_main_kb(uid))
        return

    if "firebaseio.com" not in text and "firebasedatabase.app" not in text:
        await update.message.reply_text(
            "❌ That doesn't look like a Firebase URL. Try again or /start."
        )
        return

    url = text if text.endswith("/") else text + "/"
    st.firebase_url = url
    st.phase        = "running"

    status = await update.message.reply_text(
        f"🔍 Scanning Firebase...\n`{url}`",
        parse_mode="Markdown",
    )

    asyncio.create_task(_run_flow(uid, url, ctx, status))


async def _run_flow(uid: int, firebase_url: str, ctx: ContextTypes.DEFAULT_TYPE, status_msg):
    st = _st(uid)

    # ── 1. discover devices ───────────────────────────────────────────────────
    try:
        devices = await get_devices(firebase_url)
    except Exception as e:
        await _edit(status_msg, f"❌ Firebase error: `{e}`\n\nUse /start to retry.",
                    parse_mode="Markdown")
        st.phase = "idle"
        return

    if not devices:
        await _edit(
            status_msg,
            "⚠️ No devices with phone numbers found.\n"
            "Check the URL or try another Firebase.",
            reply_markup=_main_kb(uid),
        )
        st.phase = "idle"
        return

    await _edit(
        status_msg,
        f"✅ Found *{len(devices)} device(s)* with phone numbers.\n"
        f"🔌 Proxy: `{proxy_pool.status()}`\n"
        f"🚀 Starting OTP flow...",
        parse_mode="Markdown",
    )

    # ── 2. status callback ────────────────────────────────────────────────────
    async def status_cb(done: int, failed: int, total: int, log_lines: List[str]):
        visible = "\n".join(log_lines)
        await _edit(
            status_msg,
            f"🔄 *Progress: {done + failed}/{total}*  "
            f"✅{done}  ❌{failed}\n"
            f"━━━━━━━━━━━━━━━━━━\n{visible}",
            parse_mode="Markdown",
        )

    # ── 3. run all phones ─────────────────────────────────────────────────────
    sessions = await run_all(devices, firebase_url, status_cb)

    st.phase = "idle"

    # ── 4. final summary ──────────────────────────────────────────────────────
    done   = len(sessions)
    failed = len(devices) - done

    await _edit(
        status_msg,
        f"🎉 *Run Complete!*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ Success : `{done}`\n"
        f"❌ Failed  : `{failed}`\n"
        f"📱 Total   : `{len(devices)}`\n",
        parse_mode="Markdown",
        reply_markup=_main_kb(uid),
    )

    if sessions:
        await _send_sessions(uid, ctx, sessions)


# ── Main ──────────────────────────────────────────────────────────────────────

async def _async_main():
    global _app_ref

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise SystemExit("Set BOT_TOKEN env var")

    # wire proxy → telegram ask callback
    proxy_pool.set_ask_callback(_ask_admin_for_proxy)

    req = HTTPXRequest(
        connect_timeout=30, read_timeout=30,
        write_timeout=30, pool_timeout=30,
    )
    app = Application.builder().token(BOT_TOKEN).request(req).build()
    _app_ref = app

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("proxy",       cmd_proxy))
    app.add_handler(CommandHandler("clearproxy",  cmd_clearproxy))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info("Bot starting...")
    async with app:
        await app.initialize()
        await app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        await app.start()
        log.info("Bot polling.")
        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


def main():
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
