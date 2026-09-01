"""Private chat handlers (DMs): /start, link inspection, and interactive format selector callbacks."""

import asyncio
import logging
import time
import uuid
from typing import Dict, Optional

from aiogram import Bot, F, Router, types
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message

from bot.keyboards.inline import make_format_selector_keyboard
from bot.middlewares.chat_type import IsPrivateFilter
from bot.utils.helpers import (
    escape_html,
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
from core.queue import ConcurrencyManager
from core.stats import StatsTracker

logger = logging.getLogger(__name__)

router = Router(name="private_router")
router.message.filter(IsPrivateFilter())

# In-memory dictionary storing pending download probes for user callback selections
# Schema: {download_key: {"url": str, "user_id": int, "created_at": float, "media_info": MediaInfo}}
PENDING_DOWNLOADS: Dict[str, dict] = {}


@router.message(CommandStart())
async def handle_start(message: types.Message):
    """Welcome greeting in private chat."""
    first_name = message.from_user.first_name if message.from_user else "User"
    welcome_text = (
        f"👋 Hello, {first_name}!\n\n"
        "I am your high-performance **Media Downloader Bot**.\n\n"
        "📥 **How to use:**\n"
        "1. Simply paste a link (YouTube, TikTok, Instagram, Twitter/X, Reddit, Udemy, etc.).\n"
        "2. Choose your preferred quality or audio format from the buttons.\n"
        "3. I'll download and send the file directly to you!\n\n"
        "💡 *Tip: You can also add me to group chats and supergroup topics for silent automatic downloads.*"
    )
    await message.reply(welcome_text, parse_mode="Markdown")


@router.message(F.text | F.caption)
async def handle_private_links(
    message: Message,
    downloader: DownloaderEngine,
    concurrency: ConcurrencyManager,
):
    """Handle incoming message containing media links in private chat."""
    links = extract_links_from_message(message, max_links=settings.MAX_LINKS_PER_MESSAGE)
    if not links:
        return

    user_id = message.from_user.id if message.from_user else 0

    # Check concurrency limits for user
    can_start, limit_err = await concurrency.can_start(user_id=user_id, is_group=False)
    if not can_start:
        await message.reply(f"⚠️ {limit_err}")
        return

    # Process first link with format choice
    url = links[0]
    status_msg = await message.reply("🔍 *Probing media details...*", parse_mode="Markdown")

    success, media_info, err = await downloader.probe_metadata(url)
    if not success or not media_info:
        await safe_edit_message_text(
            bot=message.bot,
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
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
    is_photo_post = media_info.is_photo
    icon = "🖼" if is_photo_post else "🎬"
    duration_str = format_duration(media_info.duration)
    size_str = format_bytes(media_info.filesize_approx) if media_info.filesize_approx else "Dynamic"
    
    text = (
        f"{icon} **{media_info.title}**\n\n"
        f"⏱ **Duration:** `{duration_str}`\n"
        f"📦 **Est. Size:** `{size_str}`\n"
        f"🌐 **Source:** `{media_info.extractor}`\n\n"
        f"Select preferred format to start download:"
    )

    keyboard = make_format_selector_keyboard(download_key, is_photo=is_photo_post)
    await safe_edit_message_text(
        bot=message.bot,
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        text=text,
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("dl:"))
async def handle_download_callback(
    call: CallbackQuery,
    downloader: DownloaderEngine,
    concurrency: ConcurrencyManager,
    stats: StatsTracker,
):
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
            await safe_delete_message(call.bot, call.message.chat.id, call.message.message_id)
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
                bot=call.bot,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"⚠️ {limit_err}",
            )
        return

    # Throttled progress callback
    last_update = 0.0
    status_chat_id = call.message.chat.id if call.message else call.from_user.id
    status_msg_id = call.message.message_id if call.message else 0

    async def on_progress(pct: float, line: str):
        nonlocal last_update
        now = time.time()
        if now - last_update >= settings.PROGRESS_UPDATE_INTERVAL_SECONDS:
            last_update = now
            bar = format_progress_bar(pct)
            text = f"⏳ **Downloading:** {bar}\n`{media_info.title[:60]}`"
            await safe_edit_message_text(
                bot=call.bot,
                chat_id=status_chat_id,
                message_id=status_msg_id,
                text=text,
            )

    if call.message:
        await safe_edit_message_text(
            bot=call.bot,
            chat_id=status_chat_id,
            message_id=status_msg_id,
            text=f"⏳ *Initializing download queue for:* `{media_info.title[:60]}`...",
        )

    # Acquire concurrency slot and download
    temp_dir_to_clean = None
    try:
        async with concurrency.acquire(user_id=call.from_user.id, is_group=False):
            if call.message:
                await safe_edit_message_text(
                    bot=call.bot,
                    chat_id=status_chat_id,
                    message_id=status_msg_id,
                    text=f"⬇️ *Downloading ({quality}):* `{media_info.title[:60]}`...",
                )

            success, result, err, temp_dir = await downloader.download(
                url=url,
                quality=quality,
                progress_callback=on_progress,
            )
            temp_dir_to_clean = temp_dir

            if not success or not result:
                if call.message:
                    await safe_edit_message_text(
                        bot=call.bot,
                        chat_id=status_chat_id,
                        message_id=status_msg_id,
                        text=f"❌ Download failed:\n`{err or 'Unknown error'}`",
                    )
                return

            # Update status to uploading
            if call.message:
                await safe_edit_message_text(
                    bot=call.bot,
                    chat_id=status_chat_id,
                    message_id=status_msg_id,
                    text="📤 *Uploading media to Telegram...*",
                )

            # Perform file upload based on media type
            title_escaped = escape_html(result.title)
            size_escaped = escape_html(format_bytes(result.file_size_bytes))
            caption = f"🎬 <b>{title_escaped}</b>\n💾 <code>{size_escaped}</code>"
            input_file = FSInputFile(result.file_path, filename=result.filename)
            thumb_input = FSInputFile(result.thumbnail_path) if result.thumbnail_path and result.thumbnail_path.exists() else None

            if result.media_type == "video":
                await call.bot.send_video(
                    chat_id=status_chat_id,
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
                await call.bot.send_audio(
                    chat_id=status_chat_id,
                    audio=input_file,
                    caption=caption,
                    title=result.title,
                    duration=result.duration,
                    thumbnail=thumb_input,
                    parse_mode="HTML",
                )
            elif result.media_type == "photo":
                photo_caption = f"🖼 <b>{title_escaped}</b>\n💾 <code>{size_escaped}</code>"
                await call.bot.send_photo(
                    chat_id=status_chat_id,
                    photo=input_file,
                    caption=photo_caption,
                    parse_mode="HTML",
                )
            elif result.media_type == "gallery":
                all_images = [result.file_path] + (result.extra_files or [])
                media_group = []
                for idx, img_p in enumerate(all_images[:10]):
                    group_caption = f"🖼 <b>{title_escaped}</b> ({len(all_images)} photos)\n💾 <code>{size_escaped}</code>" if idx == 0 else ""
                    media_group.append(
                        InputMediaPhoto(
                            media=FSInputFile(img_p),
                            caption=group_caption if group_caption else None,
                            parse_mode="HTML" if group_caption else None,
                        )
                    )
                if media_group:
                    await call.bot.send_media_group(chat_id=status_chat_id, media=media_group)
            else:
                await call.bot.send_document(
                    chat_id=status_chat_id,
                    document=input_file,
                    caption=caption,
                    parse_mode="HTML",
                )

            # Cleanup status message
            if call.message:
                await safe_delete_message(call.bot, status_chat_id, status_msg_id)

            # Record downloaded data stats
            await stats.record_download(
                user_id=call.from_user.id,
                chat_id=status_chat_id,
                is_group=False,
                file_size_bytes=result.file_size_bytes,
                media_type=result.media_type,
                quality=quality,
            )

    except Exception as e:
        logger.error("Error during download/upload callback execution: %s", e, exc_info=True)
        if call.message:
            await safe_edit_message_text(
                bot=call.bot,
                chat_id=status_chat_id,
                message_id=status_msg_id,
                text=f"❌ Processing failed: `{str(e)}`",
            )
    finally:
        PENDING_DOWNLOADS.pop(download_key, None)
        if temp_dir_to_clean:
            cleanup_directory(temp_dir_to_clean)
