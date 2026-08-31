"""Private chat handlers (DMs): /start, link inspection, and interactive format selector callbacks using Pyrogram."""

import asyncio
import logging
import os
import time
import uuid
from typing import Dict, Optional

from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import CallbackQuery, Message

from bot.keyboards.inline import make_format_selector_keyboard
from bot.middlewares.rate_limit import check_rate_limit, is_private_filter
from bot.utils.helpers import (
    create_pyrogram_upload_progress,
    extract_links_from_message,
    format_bytes,
    format_duration,
    format_progress_bar,
    safe_delete_message,
    safe_edit_message_text,
)
from config import settings
from core.cleaner import cleanup_directory
from core.downloader import DownloaderEngine, MediaInfo
from core.queue import ConcurrencyManager, TokenBucketLimiter

logger = logging.getLogger(__name__)

# In-memory dictionary storing pending download probes for user callback selections
# Schema: {download_key: {"url": str, "user_id": int, "created_at": float, "media_info": MediaInfo}}
PENDING_DOWNLOADS: Dict[str, dict] = {}


def register_private_handlers(
    app: Client,
    downloader: DownloaderEngine,
    concurrency: ConcurrencyManager,
    user_limiter: TokenBucketLimiter,
    group_limiter: TokenBucketLimiter,
):
    """Register all private chat message & callback query handlers."""

    @app.on_message(filters.command(["start"]) & is_private_filter())
    async def handle_start(client: Client, message: Message):
        """Welcome greeting in private chat."""
        first_name = message.from_user.first_name if message.from_user else "User"
        welcome_text = (
            f"👋 Hello, **{first_name}**!\n\n"
            "I am your high-performance **Media Downloader Bot** (Powered by **Pyrogram MTProto 2GB**).\n\n"
            "📥 **How to use:**\n"
            "1. Simply paste any link (YouTube, TikTok, Instagram, Twitter/X, Reddit, Udemy, etc.).\n"
            "2. Choose your preferred quality or audio format from the buttons.\n"
            "3. I will download and upload the media directly to you (supporting files up to 2GB)!\n\n"
            "💡 *Tip: You can also add me to group chats and supergroup topics for silent automatic downloads.*"
        )
        await message.reply_text(welcome_text)

    @app.on_message(filters.text & is_private_filter() & ~filters.command(["start", "help", "settings"]))
    async def handle_private_links(client: Client, message: Message):
        """Handle incoming message containing media links in private chat."""
        if not await check_rate_limit(client, message, user_limiter, group_limiter):
            return

        links = extract_links_from_message(message, max_links=settings.MAX_LINKS_PER_MESSAGE)
        if not links:
            return

        user_id = message.from_user.id if message.from_user else 0

        # Check concurrency limits for user
        can_start, limit_err = await concurrency.can_start(user_id=user_id, is_group=False)
        if not can_start:
            await message.reply_text(f"⚠️ {limit_err}")
            return

        # Process first link with format choice
        url = links[0]
        status_msg = await message.reply_text("🔍 **Probing media details...**")

        success, media_info, err = await downloader.probe_metadata(url)
        if not success or not media_info:
            await safe_edit_message_text(
                client=client,
                chat_id=message.chat.id,
                message_id=status_msg.id,
                text=f"❌ Failed to fetch link details:\n`{err or 'Unsupported URL'}`",
            )
            return

        # Store download key for callback handling
        download_key = uuid.uuid4().hex[:12]
        PENDING_DOWNLOADS[download_key] = {
            "url": url,
            "user_id": user_id,
            "created_at": time.time(),
            "media_info": media_info,
        }

        # Format info box
        duration_str = format_duration(media_info.duration)
        size_str = format_bytes(media_info.filesize_approx) if media_info.filesize_approx else "Dynamic"

        text = (
            f"🎬 **{media_info.title}**\n\n"
            f"⏱ **Duration:** `{duration_str}`\n"
            f"📦 **Est. Size:** `{size_str}`\n"
            f"🌐 **Source:** `{media_info.extractor}`\n\n"
            f"Select preferred format to start download:"
        )

        keyboard = make_format_selector_keyboard(download_key)
        await safe_edit_message_text(
            client=client,
            chat_id=message.chat.id,
            message_id=status_msg.id,
            text=text,
            reply_markup=keyboard,
        )

    @app.on_callback_query(filters.regex(r"^dl:"))
    async def handle_download_callback(client: Client, call: CallbackQuery):
        """Handle format button clicks: Best, 720p, Audio, or Cancel."""
        parts = call.data.split(":")
        if len(parts) != 3:
            await call.answer("Invalid request.", show_alert=True)
            return

        _, quality, download_key = parts
        payload = PENDING_DOWNLOADS.get(download_key)

        if quality == "cancel":
            PENDING_DOWNLOADS.pop(download_key, None)
            await call.answer("Download cancelled.")
            if call.message:
                await safe_delete_message(client, call.message.chat.id, call.message.id)
            return

        if not payload:
            await call.answer("⚠️ Session expired. Please send the link again.", show_alert=True)
            return

        # Verify user ownership of callback button
        if call.from_user.id != payload["user_id"]:
            await call.answer("❌ This download was initiated by someone else.", show_alert=True)
            return

        await call.answer("Starting download...")
        url = payload["url"]
        media_info: MediaInfo = payload["media_info"]

        # Concurrency check
        can_start, limit_err = await concurrency.can_start(user_id=call.from_user.id, is_group=False)
        if not can_start:
            if call.message:
                await safe_edit_message_text(
                    client=client,
                    chat_id=call.message.chat.id,
                    message_id=call.message.id,
                    text=f"⚠️ {limit_err}",
                )
            return

        # Throttled download progress callback
        last_update = 0.0
        status_chat_id = call.message.chat.id if call.message else call.from_user.id
        status_msg_id = call.message.id if call.message else 0

        async def on_download_progress(pct: float, line: str):
            nonlocal last_update
            now = time.time()
            if now - last_update >= settings.PROGRESS_UPDATE_INTERVAL_SECONDS:
                last_update = now
                bar = format_progress_bar(pct)
                text = f"⏳ **Downloading:** {bar}\n`{media_info.title[:60]}`"
                await safe_edit_message_text(
                    client=client,
                    chat_id=status_chat_id,
                    message_id=status_msg_id,
                    text=text,
                )

        if call.message:
            await safe_edit_message_text(
                client=client,
                chat_id=status_chat_id,
                message_id=status_msg_id,
                text=f"⏳ **Initializing download queue for:** `{media_info.title[:60]}`...",
            )

        # Acquire concurrency slot and download
        temp_dir_to_clean = None
        try:
            async with concurrency.acquire(user_id=call.from_user.id, is_group=False):
                if call.message:
                    await safe_edit_message_text(
                        client=client,
                        chat_id=status_chat_id,
                        message_id=status_msg_id,
                        text=f"⬇️ **Downloading ({quality}):** `{media_info.title[:60]}`...",
                    )

                success, result, err, temp_dir = await downloader.download(
                    url=url,
                    quality=quality,
                    progress_callback=on_download_progress,
                )
                temp_dir_to_clean = temp_dir

                if not success or not result:
                    if call.message:
                        await safe_edit_message_text(
                            client=client,
                            chat_id=status_chat_id,
                            message_id=status_msg_id,
                            text=f"❌ Download failed:\n`{err or 'Unknown error'}`",
                        )
                    return

                # Setup MTProto Upload Progress callback
                upload_progress_cb = create_pyrogram_upload_progress(
                    client=client,
                    chat_id=status_chat_id,
                    status_message_id=status_msg_id,
                    title=result.title,
                    interval_seconds=settings.PROGRESS_UPDATE_INTERVAL_SECONDS,
                )

                if call.message:
                    await safe_edit_message_text(
                        client=client,
                        chat_id=status_chat_id,
                        message_id=status_msg_id,
                        text=f"📤 **Uploading via MTProto (up to 2GB)...**\n`{result.title[:60]}`",
                    )

                # Perform native 2GB Pyrogram upload
                caption = f"🎬 **{result.title}**\n💾 `{format_bytes(result.file_size_bytes)}`"
                thumb_str = str(result.thumbnail_path) if result.thumbnail_path and result.thumbnail_path.exists() else None

                if result.media_type == "video":
                    await client.send_video(
                        chat_id=status_chat_id,
                        video=str(result.file_path),
                        caption=caption,
                        duration=result.duration or 0,
                        width=result.width or 0,
                        height=result.height or 0,
                        thumb=thumb_str,
                        supports_streaming=True,
                        progress=upload_progress_cb,
                    )
                elif result.media_type == "audio":
                    await client.send_audio(
                        chat_id=status_chat_id,
                        audio=str(result.file_path),
                        caption=caption,
                        title=result.title,
                        duration=result.duration or 0,
                        thumb=thumb_str,
                        progress=upload_progress_cb,
                    )
                else:
                    await client.send_document(
                        chat_id=status_chat_id,
                        document=str(result.file_path),
                        caption=caption,
                        thumb=thumb_str,
                        progress=upload_progress_cb,
                    )

                # Cleanup status message
                if call.message:
                    await safe_delete_message(client, status_chat_id, status_msg_id)

        except Exception as e:
            logger.error("Error during download/upload callback execution: %s", e, exc_info=True)
            if call.message:
                await safe_edit_message_text(
                    client=client,
                    chat_id=status_chat_id,
                    message_id=status_msg_id,
                    text=f"❌ Processing failed: `{str(e)}`",
                )
        finally:
            PENDING_DOWNLOADS.pop(download_key, None)
            if temp_dir_to_clean:
                cleanup_directory(temp_dir_to_clean)
