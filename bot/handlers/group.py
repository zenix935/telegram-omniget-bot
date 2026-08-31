"""Group and supergroup message listeners, forum topic preservation, and admin controls using Pyrogram."""

import asyncio
import logging
from typing import Dict, Optional

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import CallbackQuery, Message

from bot.keyboards.inline import make_group_settings_keyboard
from bot.middlewares.rate_limit import check_rate_limit, is_group_filter
from bot.utils.helpers import (
    create_pyrogram_upload_progress,
    extract_links_from_message,
    format_bytes,
    safe_delete_message,
    safe_edit_message_text,
)
from config import settings
from core.cleaner import cleanup_directory
from core.downloader import DownloaderEngine
from core.queue import ConcurrencyManager, TokenBucketLimiter

logger = logging.getLogger(__name__)

# In-memory store for group chat settings: {chat_id: {"auto_download": bool, "quality": str}}
GROUP_CONFIGS: Dict[int, dict] = {}


def get_group_config(chat_id: int) -> dict:
    """Retrieve or initialize group configuration."""
    if chat_id not in GROUP_CONFIGS:
        GROUP_CONFIGS[chat_id] = {
            "auto_download": True,
            "quality": "best",  # 'best', '720p', or 'audio'
        }
    return GROUP_CONFIGS[chat_id]


async def is_user_group_admin(client: Client, chat_id: int, user_id: int) -> bool:
    """Check if the user is an administrator or owner of the group."""
    if user_id in settings.ADMIN_IDS:
        return True
    try:
        member = await client.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception as e:
        logger.warning("Failed to check admin status for user %d in chat %d: %s", user_id, chat_id, e)
        return False


def register_group_handlers(
    app: Client,
    downloader: DownloaderEngine,
    concurrency: ConcurrencyManager,
    user_limiter: TokenBucketLimiter,
    group_limiter: TokenBucketLimiter,
):
    """Register group commands, callbacks, and automatic link listeners."""

    # ==============================================================================
    # Admin Configuration Commands
    # ==============================================================================

    @app.on_message(filters.command(["settings", "config"]) & is_group_filter())
    async def handle_group_settings(client: Client, message: Message):
        """Open interactive settings panel for group administrators."""
        user_id = message.from_user.id if message.from_user else 0
        if not await is_user_group_admin(client, message.chat.id, user_id):
            await message.reply_text("⛔ Only group administrators can configure bot settings.")
            return

        cfg = get_group_config(message.chat.id)
        text = (
            f"⚙️ **Settings for {message.chat.title}**\n\n"
            f"• **Auto-Download:** `{'Enabled' if cfg['auto_download'] else 'Disabled'}`\n"
            f"• **Default Quality:** `{cfg['quality']}`\n\n"
            f"Use the buttons below to change settings:"
        )
        keyboard = make_group_settings_keyboard(cfg["auto_download"], cfg["quality"])
        await message.reply_text(
            text,
            reply_markup=keyboard,
            message_thread_id=message.message_thread_id,
        )

    @app.on_callback_query(filters.regex(r"^grp_set:"))
    async def handle_group_settings_callback(client: Client, call: CallbackQuery):
        """Handle admin buttons in group settings."""
        if not call.message:
            await call.answer()
            return

        user_id = call.from_user.id
        chat_id = call.message.chat.id

        if not await is_user_group_admin(client, chat_id, user_id):
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
            await safe_delete_message(client, chat_id, call.message.id)
            await call.answer()
            return

        text = (
            f"⚙️ **Settings for {call.message.chat.title}**\n\n"
            f"• **Auto-Download:** `{'Enabled' if cfg['auto_download'] else 'Disabled'}`\n"
            f"• **Default Quality:** `{cfg['quality']}`\n\n"
            f"Use the buttons below to change settings:"
        )
        keyboard = make_group_settings_keyboard(cfg["auto_download"], cfg["quality"])
        await safe_edit_message_text(
            client=client,
            chat_id=chat_id,
            message_id=call.message.id,
            text=text,
            reply_markup=keyboard,
        )

    @app.on_message(filters.command(["toggle_download"]) & is_group_filter())
    async def handle_toggle_download(client: Client, message: Message):
        """Quick command to toggle automatic downloads on/off."""
        user_id = message.from_user.id if message.from_user else 0
        if not await is_user_group_admin(client, message.chat.id, user_id):
            await message.reply_text("⛔ Only group administrators can use this command.")
            return

        cfg = get_group_config(message.chat.id)
        cfg["auto_download"] = not cfg["auto_download"]
        state_str = "🟢 Enabled" if cfg["auto_download"] else "🔴 Disabled"
        await message.reply_text(
            f"Auto-download is now {state_str} for this group.",
            message_thread_id=message.message_thread_id,
        )

    # ==============================================================================
    # Silent & Zero-Clutter Group Link Listener
    # ==============================================================================

    @app.on_message(filters.text & is_group_filter() & ~filters.command(["settings", "config", "toggle_download", "help"]))
    async def handle_group_links(client: Client, message: Message):
        """
        Listens for supported links in groups/supergroups and forum topics.
        Downloads optimal quality and delivers media directly replying to the sender via MTProto 2GB,
        then automatically deletes the temporary status message.
        """
        cfg = get_group_config(message.chat.id)
        if not cfg.get("auto_download", True):
            return

        if not await check_rate_limit(client, message, user_limiter, group_limiter):
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

        url = links[0]
        quality = cfg.get("quality", "best")

        # Step 1: Send clean status reply (preserved in same thread/topic)
        status_msg = None
        try:
            status_msg = await message.reply_text(
                "⏳ **Processing media (MTProto 2GB)...**",
                message_thread_id=thread_id,
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
                        await safe_edit_message_text(
                            client=client,
                            chat_id=chat_id,
                            message_id=status_msg.id,
                            text=f"❌ Failed to download media:\n`{err or 'Unsupported URL'}`",
                        )
                        await asyncio.sleep(10)
                        await safe_delete_message(client, chat_id, status_msg.id)
                    return

                # Step 3: Send downloaded media directly replying to original user message with 2GB capability
                caption = f"🎬 **{result.title}**\n💾 `{format_bytes(result.file_size_bytes)}`"
                thumb_str = str(result.thumbnail_path) if result.thumbnail_path and result.thumbnail_path.exists() else None

                if result.media_type == "video":
                    await message.reply_video(
                        video=str(result.file_path),
                        caption=caption,
                        duration=result.duration or 0,
                        width=result.width or 0,
                        height=result.height or 0,
                        thumb=thumb_str,
                        supports_streaming=True,
                        message_thread_id=thread_id,
                    )
                elif result.media_type == "audio":
                    await message.reply_audio(
                        audio=str(result.file_path),
                        caption=caption,
                        title=result.title,
                        duration=result.duration or 0,
                        thumb=thumb_str,
                        message_thread_id=thread_id,
                    )
                else:
                    await message.reply_document(
                        document=str(result.file_path),
                        caption=caption,
                        thumb=thumb_str,
                        message_thread_id=thread_id,
                    )

                # Step 4: Zero-clutter auto-cleanup of temporary status message
                if status_msg:
                    await safe_delete_message(client, chat_id, status_msg.id)

        except Exception as e:
            logger.error("Error processing group download for %s in chat %d: %s", url, chat_id, e, exc_info=True)
            if status_msg:
                await safe_delete_message(client, chat_id, status_msg.id)
        finally:
            if temp_dir_to_clean:
                cleanup_directory(temp_dir_to_clean)
