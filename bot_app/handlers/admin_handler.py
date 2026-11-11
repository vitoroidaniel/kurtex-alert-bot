"""handlers/admin_handler.py -- Admin report commands + user management."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from backend.core.app_time import (
    CENTRAL_TIMEZONE_LABEL,
    MOLDOVA_TIMEZONE_LABEL,
    chicago_now,
    chicago_timestamp,
    moldova_now,
)
from backend.storage.case_store import (
    async_get_cases_between as get_cases_between,
    async_get_cases_this_week as get_cases_this_week,
    async_get_cases_today as get_cases_today,
)
from backend.storage.user_store import (
    VALID_ROLES,
    add_user,
    edit_role,
    get_all_users,
    get_user,
    has_role,
    remove_user,
)
from bot_app.telegram_utils import (
    esc_html,
    safe_answer,
    safe_edit_message_text,
    safe_reply_text,
    safe_send_message,
)

logger = logging.getLogger(__name__)
BOT_NAME = "Kurtex Alert Bot"


def _is_main_admin(user_id: int) -> bool:
    return has_role(user_id, "super_admin", "developer")


def _can_manage(user_id: int) -> bool:
    return has_role(user_id, "developer", "super_admin")


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = chicago_timestamp(iso)
        return dt.strftime("%H:%M") if dt else iso[:16]
    except Exception:
        return str(iso)[:16]


def _build_daily_report(cases: list[dict], title: str) -> str:
    total = len(cases)
    assigned = [c for c in cases if c.get("status") in ("assigned", "reported", "done")]
    done = [c for c in cases if c.get("status") == "done"]
    missed = [c for c in cases if c.get("status") == "missed"]
    open_ = [c for c in cases if c.get("status") == "open"]

    agent_counts: dict[str, int] = defaultdict(int)
    for c in assigned:
        if c.get("agent_name"):
            agent_counts[c["agent_name"]] += 1

    lines = [
        f"<b>{esc_html(title)}</b>\n",
        f"Total Alerts: {total}",
        f"Assigned: {len(assigned)}",
        f"Resolved: {len(done)}",
        f"Missed: {len(missed)}",
        f"Open: {len(open_)}",
    ]

    if agent_counts:
        lines.append("\n<b>Agent Activity:</b>")
        for agent, count in sorted(agent_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {esc_html(agent)}: {count} case(s)")

    if missed:
        lines.append("\n<b>Unresolved Alerts:</b>")
        for c in missed[:30]:
            lines.append(
                f"  {esc_html(_fmt_dt(c.get('opened_at')))} — "
                f"{esc_html(c.get('driver_name'))} ({esc_html(c.get('group_name'))})"
            )
        if len(missed) > 30:
            lines.append(f"  ... and {len(missed) - 30} more")

    return "\n".join(lines)


# ── /report ───────────────────────────────────────────────────────────────────

async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await asyncio.to_thread(_is_main_admin, user.id):
        await safe_reply_text(update.message, "Access denied.", parse_mode=None)
        return
    cases = await get_cases_today()
    today = chicago_now().strftime("%B %d, %Y")
    report = _build_daily_report(cases, f"Daily Report — {today} {CENTRAL_TIMEZONE_LABEL}")
    await safe_reply_text(update.message, report)


# ── /leaderboard ──────────────────────────────────────────────────────────────

async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await asyncio.to_thread(_is_main_admin, user.id):
        await safe_reply_text(update.message, "Access denied.", parse_mode=None)
        return
    cases = await get_cases_this_week()
    if not cases:
        await safe_reply_text(update.message, "No activity recorded this week yet.", parse_mode=None)
        return

    agent_stats: dict[str, dict] = defaultdict(lambda: {"count": 0})
    for c in cases:
        if c.get("agent_name") and c.get("status") in ("assigned", "reported", "done"):
            agent_stats[c["agent_name"]]["count"] += 1

    if not agent_stats:
        await safe_reply_text(update.message, "No assigned cases this week.", parse_mode=None)
        return

    sorted_agents = sorted(agent_stats.items(), key=lambda x: -x[1]["count"])
    medals = ["🥇", "🥈", "🥉"]
    lines = ["<b>Weekly Leaderboard</b>\n"]
    for i, (name, stats) in enumerate(sorted_agents):
        medal = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{medal} <b>{esc_html(name)}</b> — {stats['count']} cases")

    await safe_reply_text(update.message, "\n".join(lines))


# ── /missed ───────────────────────────────────────────────────────────────────

async def cmd_missed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await asyncio.to_thread(_is_main_admin, user.id):
        await safe_reply_text(update.message, "Access denied.", parse_mode=None)
        return
    cases = await get_cases_today()
    missed = [c for c in cases if c.get("status") == "missed"]
    if not missed:
        await safe_reply_text(update.message, "✅ All alerts handled today. Great job!", parse_mode=None)
        return
    lines = [f"<b>Missed Alerts — {len(missed)} today</b>\n"]
    for c in missed[:30]:
        lines.append(f"{esc_html(_fmt_dt(c.get('opened_at')))} — {esc_html(c.get('driver_name'))}")
        lines.append(f"   {esc_html(c.get('group_name'))}")
    if len(missed) > 30:
        lines.append(f"\n... and {len(missed) - 30} more")
    await safe_reply_text(update.message, "\n".join(lines))


# ── Daily report (called by scheduler) ───────────────────────────────────────

async def send_daily_report(bot, chat_id: int) -> None:
    now_md = moldova_now()
    end = now_md.replace(hour=14, minute=30, second=0, microsecond=0)
    if now_md < end:
        end -= timedelta(days=1)
    start = end - timedelta(days=1)
    cases = await get_cases_between(start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    period = f"{start.strftime('%b %d, %H:%M')} - {end.strftime('%b %d, %H:%M')} {MOLDOVA_TIMEZONE_LABEL}"
    report = _build_daily_report(cases, f"Daily Operations Report — {period}")
    try:
        await safe_send_message(bot, chat_id, report)
        logger.info("Daily report sent to %s for %s", chat_id, period)
    except Exception as exc:
        logger.error("Failed to send daily report: %s", exc)


# ── User management commands ──────────────────────────────────────────────────

async def cmd_adduser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await asyncio.to_thread(_can_manage, user.id):
        await safe_reply_text(update.message, "Access denied.", parse_mode=None)
        return
    args = ctx.args
    if len(args) < 3:
        await safe_reply_text(
            update.message,
            "Usage: <code>/adduser &lt;user_id&gt; &lt;name&gt; &lt;role&gt;</code>\n"
            f"Roles: <code>{esc_html(' | '.join(sorted(VALID_ROLES)))}</code>\n\n"
            "Or just <b>forward any message</b> from the user and I'll ask for the role.",
        )
        return
    try:
        uid = int(args[0])
    except ValueError:
        await safe_reply_text(update.message, "Invalid user_id — must be a number.", parse_mode=None)
        return
    name = args[1]
    role = args[2].lower()
    if await asyncio.to_thread(add_user, uid, name, "", role):
        await safe_reply_text(
            update.message,
            f"✅ Added <b>{esc_html(name)}</b> (ID: <code>{uid}</code>) as <b>{esc_html(role)}</b>.",
        )
    else:
        await safe_reply_text(
            update.message,
            f"❌ Invalid role. Use: <code>{esc_html(' | '.join(sorted(VALID_ROLES)))}</code>",
        )


async def cmd_removeuser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await asyncio.to_thread(_can_manage, user.id):
        await safe_reply_text(update.message, "Access denied.", parse_mode=None)
        return
    if not ctx.args:
        await safe_reply_text(update.message, "Usage: <code>/removeuser &lt;user_id&gt;</code>")
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await safe_reply_text(update.message, "❌ Invalid user_id.", parse_mode=None)
        return
    existing = await asyncio.to_thread(get_user, uid)
    if await asyncio.to_thread(remove_user, uid):
        name = existing["name"] if existing else str(uid)
        await safe_reply_text(update.message, f"✅ <b>{esc_html(name)}</b> (ID: <code>{uid}</code>) removed.")
    else:
        await safe_reply_text(update.message, f"❌ User <code>{uid}</code> not found.")


async def cmd_editrole(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await asyncio.to_thread(_can_manage, user.id):
        await safe_reply_text(update.message, "Access denied.", parse_mode=None)
        return
    if len(ctx.args) < 2:
        await safe_reply_text(
            update.message,
            f"Usage: <code>/editrole &lt;user_id&gt; &lt;role&gt;</code>\n"
            f"Roles: <code>{esc_html(' | '.join(sorted(VALID_ROLES)))}</code>",
        )
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await safe_reply_text(update.message, "❌ Invalid user_id.", parse_mode=None)
        return
    role = ctx.args[1].lower()
    existing = await asyncio.to_thread(get_user, uid)
    if not existing:
        await safe_reply_text(update.message, f"❌ User <code>{uid}</code> not found.")
        return
    if await asyncio.to_thread(edit_role, uid, role):
        await safe_reply_text(
            update.message,
            f"✅ <b>{esc_html(existing['name'])}</b> is now <b>{esc_html(role)}</b>.",
        )
    else:
        await safe_reply_text(
            update.message,
            f"❌ Invalid role. Use: <code>{esc_html(' | '.join(sorted(VALID_ROLES)))}</code>",
        )


async def cmd_listusers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await asyncio.to_thread(_can_manage, user.id):
        await safe_reply_text(update.message, "Access denied.", parse_mode=None)
        return
    users = await asyncio.to_thread(get_all_users)
    if not users:
        await safe_reply_text(update.message, "No users in the system yet.", parse_mode=None)
        return

    role_order = {"developer": 0, "super_admin": 1, "agent": 2}
    sorted_users = sorted(users.items(), key=lambda x: role_order.get(x[1]["role"], 9))

    role_icons = {"developer": "🛠", "super_admin": "⭐", "agent": "👤"}
    lines = ["User List\n"]
    for uid, u in sorted_users:
        icon = role_icons.get(u["role"], "•")
        handle = f"@{u['username']}" if u.get("username") else "—"
        lines.append(f"{icon} {u['name']} ({handle})\n   ID: {uid} — {u['role']}")

    await safe_reply_text(update.message, "\n".join(lines), parse_mode=None)


# ── Forward a message -> add user flow ────────────────────────────────────────

async def handle_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await asyncio.to_thread(_can_manage, user.id):
        return

    msg = update.message
    fwd_user = None
    origin = getattr(msg, "forward_origin", None)
    if origin:
        fwd_user = getattr(origin, "sender_user", None)
    if not fwd_user and getattr(msg, "forward_from", None):
        fwd_user = msg.forward_from

    if not fwd_user:
        await safe_reply_text(
            msg,
            "⚠️ Couldn't read the user from that forward.\n\n"
            "The person may have <b>Forwarding Privacy</b> enabled in Telegram settings.\n"
            "Ask them to send <code>/start</code> to the bot directly — then try again.\n\n"
            "Or add manually: <code>/adduser &lt;user_id&gt; &lt;name&gt; &lt;role&gt;</code>",
        )
        return

    uid = fwd_user.id
    name = fwd_user.full_name
    username = fwd_user.username or ""
    handle = f"@{esc_html(username)}" if username else f"ID: <code>{uid}</code>"

    existing = await asyncio.to_thread(get_user, uid)
    status_line = ""
    if existing:
        status_line = f"\n\n⚠️ Already registered as <b>{esc_html(existing['role'])}</b> — selecting a role will update it."

    ctx.user_data["pending_add"] = {
        "uid": uid,
        "name": name,
        "username": username,
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 Agent",      callback_data="addrole|agent"),
            InlineKeyboardButton("⭐ Super Admin", callback_data="addrole|super_admin"),
            InlineKeyboardButton("🛠 Developer",   callback_data="addrole|developer"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="addrole|cancel")],
    ])

    await safe_reply_text(
        msg,
        f"👤 <b>{esc_html(name)}</b> ({handle})\n\nSelect a role for this user:{status_line}",
        reply_markup=keyboard,
    )


async def cb_addrole(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    role = query.data.split("|")[1]

    if role == "cancel":
        ctx.user_data.pop("pending_add", None)
        await safe_edit_message_text(query, "Cancelled.", parse_mode=None)
        return

    pending = ctx.user_data.get("pending_add")
    if not pending:
        await safe_edit_message_text(query, "⚠️ Session expired. Forward the message again.", parse_mode=None)
        return

    uid, name, username = pending["uid"], pending["name"], pending["username"]
    await asyncio.to_thread(add_user, uid, name, username, role)
    ctx.user_data.pop("pending_add", None)

    handle = f"@{esc_html(username)}" if username else f"ID: <code>{uid}</code>"
    role_icons = {"developer": "🛠", "super_admin": "⭐", "agent": "👤"}
    icon = role_icons.get(role, "•")

    await safe_edit_message_text(
        query,
        f"✅ {icon} <b>{esc_html(name)}</b> ({handle}) added as <b>{esc_html(role)}</b>.",
    )
