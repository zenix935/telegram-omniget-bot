"""Link extraction from Pyrogram messages, progress bar builders, and safe upload utilities."""

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Callable, Coroutine, List, Optional, Tuple, Any

from pyrogram import Client
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message

from config import settings
from core.security import is_supported_media_domain, validate_url

logger = logging.getLogger(__name__)

# URL regex for fallback when message entities are not present
RAW_URL_REGEX = re.compile(
    r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)",
    re.IGNORECASE,
)


def extract_links_from_message(message: Message, max_links: int = 2) -> List[str]:
    """
    Extract supported URLs from Pyrogram Message entities (URL or TEXT_LINK)
    or fallback to regex extraction across message text/caption.
    """
    found_urls: List[str] = []
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []

    # 1. Process Message entities first (most reliable)
    for entity in entities:
        if entity.type == MessageEntityType.URL:
            # Slicing using UTF-16 code units (safe offset for emojis & multi-byte chars)
            try:
                encoded = text.encode("utf-16-le")
                url_str = encoded[entity.offset * 2 : (entity.offset + entity.length) * 2].decode("utf-16-le")
            except Exception:
                url_str = text[entity.offset : entity.offset + entity.length]
            if url_str and url_str not in found_urls:
                found_urls.append(url_str.strip())
        elif entity.type == MessageEntityType.TEXT_LINK and entity.url:
            if entity.url not in found_urls:
                found_urls.append(entity.url.strip())

    # 2. Fallback or augment with regex if no entities found or raw link
    if not found_urls and text:
        matches = RAW_URL_REGEX.findall(text)
        for m in matches:
            if m not in found_urls:
                found_urls.append(m.strip())

    # 3. Filter valid & non-SSRF URLs
    valid_links: List[str] = []
    for link in found_urls:
        valid, _ = validate_url(link, resolve_dns=False)
        if valid:
            valid_links.append(link)
        if len(valid_links) >= max_links:
            break

    return valid_links


def format_progress_bar(percentage: float, length: int = 12) -> str:
    """Generate a clean unicode progress bar string."""
    percentage = max(0.0, min(100.0, percentage))
    filled_length = int(length * percentage // 100)
    bar = "█" * filled_length + "░" * (length - filled_length)
    return f"[{bar}] {percentage:.1f}%"


def format_bytes(size_bytes: int) -> str:
    """Convert bytes into human readable string (KB, MB, GB)."""
    if not size_bytes or size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    val = float(size_bytes)
    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1
    return f"{val:.1f} {units[unit_idx]}"


def format_duration(seconds: Optional[int]) -> str:
    """Convert duration in seconds to HH:MM:SS or MM:SS."""
    if not seconds or seconds <= 0:
        return "--:--"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


async def safe_delete_message(client: Client, chat_id: int, message_id: int) -> None:
    """Safely delete a message without raising exceptions."""
    try:
        await client.delete_messages(chat_id=chat_id, message_ids=message_id)
    except Exception as e:
        logger.debug("Could not delete message %d in chat %d: %s", message_id, chat_id, e)


async def safe_edit_message_text(
    client: Client,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup=None,
) -> bool:
    """Safely edit a message text while ignoring unchanged content or deleted message errors."""
    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
        return True
    except Exception as e:
        err_msg = str(e).lower()
        if "message_not_modified" in err_msg or "message is not modified" in err_msg:
            return True
        if "flood" in err_msg:
            logger.warning("Flood limit hit while editing message: %s", e)
        else:
            logger.debug("Edit message failed (%d in %d): %s", message_id, chat_id, e)
        return False


def create_pyrogram_upload_progress(
    client: Client,
    chat_id: int,
    status_message_id: int,
    title: str,
    interval_seconds: float = 4.0,
):
    """
    Creates a throttled MTProto upload progress callback compatible with Pyrogram's
    send_video / send_audio progress=(func, *args) signature.
    """
    last_update_time = 0.0

    async def progress_callback(current: int, total: int):
        nonlocal last_update_time
        now = time.time()
        if total > 0 and (now - last_update_time >= interval_seconds or current == total):
            last_update_time = now
            pct = (current / total) * 100.0
            bar = format_progress_bar(pct)
            text = (
                f"📤 **Uploading to Telegram (MTProto 2GB):** {bar}\n"
                f"`{format_bytes(current)} / {format_bytes(total)}`\n"
                f"`{title[:60]}`"
            )
            await safe_edit_message_text(
                client=client,
                chat_id=chat_id,
                message_id=status_message_id,
                text=text,
            )

    return progress_callback
