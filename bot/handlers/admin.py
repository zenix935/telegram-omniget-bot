"""Admin dashboard and metrics reporting handlers."""

import html
import logging
from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.keyboards.inline import make_admin_panel_keyboard
from bot.utils.helpers import format_bytes, safe_delete_message, safe_edit_message_text
from config import settings
from core.stats import StatsTracker

logger = logging.getLogger(__name__)

router = Router(name="admin_router")


def is_admin(user_id: int) -> bool:
    """Check if the user is in ADMIN_IDS."""
    return user_id in settings.ADMIN_IDS


def format_stats_panel_text(stats: dict) -> str:
    """Generate formatted HTML text for the admin data usage panel."""
    s_1d = stats.get("1d", {})
    s_7d = stats.get("7d", {})
    s_30d = stats.get("30d", {})
    s_all = stats.get("all", {})

    b_1d = html.escape(format_bytes(s_1d.get("total_bytes", 0)))
    c_1d = s_1d.get("total_count", 0)
    u_1d = s_1d.get("unique_users", 0)

    b_7d = html.escape(format_bytes(s_7d.get("total_bytes", 0)))
    c_7d = s_7d.get("total_count", 0)
    u_7d = s_7d.get("unique_users", 0)

    b_30d = html.escape(format_bytes(s_30d.get("total_bytes", 0)))
    c_30d = s_30d.get("total_count", 0)
    u_30d = s_30d.get("unique_users", 0)

    b_all = html.escape(format_bytes(s_all.get("total_bytes", 0)))
    c_all = s_all.get("total_count", 0)
    u_all = s_all.get("unique_users", 0)
    g_all = s_all.get("unique_chats", 0)

    text = (
        "📊 <b>Admin Data Usage & Traffic Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⏱ <b>Last 24 Hours (1 Day):</b>\n"
        f"  • <b>Data Used:</b> <code>{b_1d}</code>\n"
        f"  • <b>Completed:</b> <code>{c_1d} downloads</code> ({u_1d} users)\n\n"
        "📅 <b>Last 7 Days:</b>\n"
        f"  • <b>Data Used:</b> <code>{b_7d}</code>\n"
        f"  • <b>Completed:</b> <code>{c_7d} downloads</code> ({u_7d} users)\n\n"
        "🗓 <b>Last 30 Days (1 Month):</b>\n"
        f"  • <b>Data Used:</b> <code>{b_30d}</code>\n"
        f"  • <b>Completed:</b> <code>{c_30d} downloads</code> ({u_30d} users)\n\n"
        "🌐 <b>All-Time Total:</b>\n"
        f"  • <b>Total Data:</b> <code>{b_all}</code>\n"
        f"  • <b>Total Media:</b> <code>{c_all} files</code>\n"
        f"  • <b>Unique Users:</b> <code>{u_all}</code>\n"
        f"  • <b>Active Chats:</b> <code>{g_all}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Updates in real-time on every media completion.</i>"
    )
    return text


@router.message(Command("admin", "stats", "usage"))
async def handle_admin_stats(message: Message, stats: StatsTracker):
    """Render admin statistics dashboard for authorized bot admins."""
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.reply("⛔ <b>Access Denied:</b> This command is restricted to administrators.", parse_mode="HTML")
        return

    all_stats = await stats.get_all_stats()
    text = format_stats_panel_text(all_stats)
    keyboard = make_admin_panel_keyboard()

    await message.reply(
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("adm_panel:"))
async def handle_admin_panel_callbacks(call: CallbackQuery, stats: StatsTracker):
    """Handle refresh and navigation buttons in the admin panel."""
    user_id = call.from_user.id
    if not is_admin(user_id):
        await call.answer("⛔ Access Denied.", show_alert=True)
        return

    action = call.data.split(":")[1]

    if action == "refresh":
        all_stats = await stats.get_all_stats()
        text = format_stats_panel_text(all_stats)
        keyboard = make_admin_panel_keyboard()
        if call.message:
            await safe_edit_message_text(
                bot=call.bot,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=text,
                reply_markup=keyboard,
            )
        await call.answer("🔄 Data usage refreshed!")

    elif action == "top":
        top_users = await stats.get_top_users(limit=5)
        lines = ["🏆 <b>Top Bandwidth Consumers (All-Time):</b>\n"]
        if not top_users:
            lines.append("<i>No download activity recorded yet.</i>")
        else:
            for idx, (uid, u_bytes, u_cnt) in enumerate(top_users, 1):
                b_str = html.escape(format_bytes(u_bytes))
                lines.append(f"{idx}. User <code>{uid}</code>: <b>{b_str}</b> ({u_cnt} downloads)")
        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        top_text = "\n".join(lines)
        keyboard = make_admin_panel_keyboard()
        if call.message:
            await safe_edit_message_text(
                bot=call.bot,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=top_text,
                reply_markup=keyboard,
            )
        await call.answer()

    elif action == "close":
        if call.message:
            await safe_delete_message(call.bot, call.message.chat.id, call.message.message_id)
        await call.answer()
