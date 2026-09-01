"""Group and supergroup message listeners, forum topic preservation, and admin controls."""

import asyncio
import logging
from typing import Dict, Optional

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from bot.keyboards.inline import make_group_settings_keyboard
from bot.middlewares.chat_type import IsGroupFilter
from bot.utils.helpers import (
    escape_html,
    extract_links_from_message,
    format_bytes,
    safe_delete_message,
    safe_edit_message_text,
)
from config import settings
from core.cleaner import cleanup_directory
from core.downloader import DownloaderEngine
from core.queue import ConcurrencyManager
from core.stats import StatsTracker

logger = logging.getLogger(__name__)

router = Router(name="group_router")
router.message.filter(IsGroupFilter())

# In-memory store for group chat settings: {chat_id: {"auto_download": bool, "quality": str}}
# In production with Redis, this can be synced to Redis key "group_cfg:<chat_id>"
GROUP_CONFIGS: Dict[int, dict] = {}


def get_group_config(chat_id: int) -> dict:
    """Retrieve or initialize group configuration."""
    if chat_id not in GROUP_CONFIGS:
        GROUP_CONFIGS[chat_id] = {
            "auto_download": True,
            "quality": "720p",  # 'best', '720p', or 'audio'
        }
    return GROUP_CONFIGS[chat_id]


async def is_user_group_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Check if the user is an administrator or owner of the group."""
    if user_id in settings.ADMIN_IDS:
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ("creator", "administrator")
    except Exception as e:
        logger.warning("Failed to check admin status for user %d in chat %d: %s", user_id, chat_id, e)
        return False


# ==============================================================================
# Admin Configuration Commands
# ==============================================================================

@router.message(Command("settings", "config"))
async def handle_group_settings(message: Message, bot: Bot):
    """Open interactive settings panel for group administrators."""
    user_id = message.from_user.id if message.from_user else 0
    if not await is_user_group_admin(bot, message.chat.id, user_id):
        await message.reply("⛔ Only group administrators can configure bot settings.")
        return

    cfg = get_group_config(message.chat.id)
    text = (
        f"⚙️ **Settings for {message.chat.title}**\n\n"
        f"• **Auto-Download:** `{'Enabled' if cfg['auto_download'] else 'Disabled'}`\n"
        f"• **Default Quality:** `{cfg['quality']}`\n\n"
        f"Use the buttons below to change settings:"
    )
    keyboard = make_group_settings_keyboard(cfg["auto_download"], cfg["quality"])
    await message.reply(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("grp_set:"))
async def handle_group_settings_callback(call: CallbackQuery, bot: Bot):
    """Handle admin buttons in group settings."""
    if not call.message:
        await call.answer()
        return

    user_id = call.from_user.id
    chat_id = call.message.chat.id

    if not await is_user_group_admin(bot, chat_id, user_id):
        await call.answer("⛔ Only group administrators can change settings.", show_alert=True)
        return

    parts = call.data.split(":")
    action = parts[1]
    cfg = get_group_config(chat_id)

    if action == "toggle":
        cfg["auto_download"] = not cfg["auto_download"]
        await call.answer(f"Auto-download {'Enabled' if cfg['auto_download'] else 'Disabled'}")
    elif action == "qual" and len(parts) == 3:
        cfg["quality"] = parts[2]
        await call.answer(f"Default quality set to {parts[2]}")
    elif action == "close":
        await safe_delete_message(bot, chat_id, call.message.message_id)
        await call.answer()
        return

    # Update settings message UI
    text = (
        f"⚙️ **Settings for {call.message.chat.title}**\n\n"
        f"• **Auto-Download:** `{'Enabled' if cfg['auto_download'] else 'Disabled'}`\n"
        f"• **Default Quality:** `{cfg['quality']}`\n\n"
        f"Use the buttons below to change settings:"
    )
    keyboard = make_group_settings_keyboard(cfg["auto_download"], cfg["quality"])
    await safe_edit_message_text(
        bot=bot,
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=text,
        reply_markup=keyboard,
    )


@router.message(Command("toggle_download"))
async def handle_toggle_download(message: Message, bot: Bot):
    """Quick command to toggle automatic downloads on/off."""
    user_id = message.from_user.id if message.from_user else 0
    if not await is_user_group_admin(bot, message.chat.id, user_id):
        await message.reply("⛔ Only group administrators can use this command.")
        return

    cfg = get_group_config(message.chat.id)
    cfg["auto_download"] = not cfg["auto_download"]
    state_str = "🟢 Enabled" if cfg["auto_download"] else "🔴 Disabled"
    await message.reply(
        f"Auto-download is now {state_str} for this group.",
    )


# ==============================================================================
# Silent & Zero-Clutter Group Link Listener
# ==============================================================================

@router.message(F.text | F.caption)
async def handle_group_links(
    message: Message,
    downloader: DownloaderEngine,
    concurrency: ConcurrencyManager,
    stats: StatsTracker,
):
    """
    Listens for supported links in groups/supergroups and forum topics.
    Downloads optimal quality and delivers media directly replying to the sender,
    then automatically deletes the temporary status message.
    """
    cfg = get_group_config(message.chat.id)
    if not cfg.get("auto_download", True):
        return

    # Extract valid media links (max 1-2 to prevent chat flooding)
    links = extract_links_from_message(message, max_links=settings.MAX_LINKS_PER_MESSAGE)
    if not links:
        return

    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    thread_id = message.message_thread_id  # Thread/Topic ID preservation

    # Check concurrency limits for user & group
    can_start, limit_err = await concurrency.can_start(user_id=user_id, chat_id=chat_id, is_group=True)
    if not can_start:
        logger.info("Group %d / User %d hit concurrency limit: %s", chat_id, user_id, limit_err)
        return

    # Process first valid link
    url = links[0]
    quality = cfg.get("quality", "best")

    # Step 1: Send clean status reply
    status_msg = None
    try:
        status_msg = await message.reply(
            "⏳ *Processing media...*",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning("Failed to send status message in chat %d: %s", chat_id, e)

    temp_dir_to_clean = None
    try:
        # Step 2: Acquire concurrency slot and download
        async with concurrency.acquire(user_id=user_id, chat_id=chat_id, is_group=True):
            success, result, err, temp_dir = await downloader.download(
                url=url,
                quality=quality,
            )
            temp_dir_to_clean = temp_dir

            if not success or not result:
                logger.info("Download failed for URL %s in group %d: %s", url, chat_id, err)
                if status_msg:
                    # Inform user briefly then auto-delete status after 10s
                    await safe_edit_message_text(
                        bot=message.bot,
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                        text=f"❌ Failed to download media:\n`{err or 'Unsupported URL'}`",
                    )
                    await asyncio.sleep(10)
                    await safe_delete_message(message.bot, chat_id, status_msg.message_id)
                return

            # Step 3: Send downloaded media directly replying to original user message
            title_escaped = escape_html(result.title)
            size_escaped = escape_html(format_bytes(result.file_size_bytes))
            caption = f"🎬 <b>{title_escaped}</b>\n💾 <code>{size_escaped}</code>"
            input_file = FSInputFile(result.file_path, filename=result.filename)
            thumb_input = FSInputFile(result.thumbnail_path) if result.thumbnail_path and result.thumbnail_path.exists() else None

            if result.media_type == "video":
                await message.reply_video(
                    video=input_file,
                    caption=caption,
                    duration=result.duration,
                    width=result.width,
                    height=result.height,
                    thumbnail=thumb_input,
                    supports_streaming=True,
                    parse_mode="HTML",
                )
            elif result.media_type == "audio":
                await message.reply_audio(
                    audio=input_file,
                    caption=caption,
                    title=result.title,
                    duration=result.duration,
                    thumbnail=thumb_input,
                    parse_mode="HTML",
                )
            else:
                await message.reply_document(
                    document=input_file,
                    caption=caption,
                    parse_mode="HTML",
                )

            # Step 4: Zero-clutter auto-cleanup of temporary status message
            if status_msg:
                await safe_delete_message(message.bot, chat_id, status_msg.message_id)

            # Record downloaded data stats
            await stats.record_download(
                user_id=user_id,
                chat_id=chat_id,
                is_group=True,
                file_size_bytes=result.file_size_bytes,
                media_type=result.media_type,
                quality=quality,
            )

    except Exception as e:
        logger.error("Error processing group download for %s in chat %d: %s", url, chat_id, e, exc_info=True)
        if status_msg:
            await safe_delete_message(message.bot, chat_id, status_msg.message_id)
    finally:
        if temp_dir_to_clean:
            cleanup_directory(temp_dir_to_clean)
