"""Custom filters and rate limiting helpers for Pyrogram."""

import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message

from config import settings
from core.queue import TokenBucketLimiter

logger = logging.getLogger(__name__)


def is_group_filter():
    """Filter ensuring update is strictly from a group or supergroup chat."""
    return filters.chat_type([ChatType.GROUP, ChatType.SUPERGROUP])


def is_private_filter():
    """Filter ensuring update is strictly from a private chat."""
    return filters.chat_type(ChatType.PRIVATE)


async def check_rate_limit(
    client: Client,
    message: Message,
    user_limiter: TokenBucketLimiter,
    group_limiter: TokenBucketLimiter,
) -> bool:
    """
    Check if the incoming message is within token bucket rate limits.
    Returns True if allowed, False if rate limited.
    """
    if not message.from_user:
        return True

    # Global admins bypass rate limiting
    if message.from_user.id in settings.ADMIN_IDS:
        return True

    if message.chat.type == ChatType.PRIVATE:
        user_key = f"user:{message.from_user.id}"
        allowed = await user_limiter.acquire(user_key)
        if not allowed:
            retry_after = await user_limiter.get_retry_after(user_key)
            logger.info("User %d rate limited in DM (retry after %.1fs)", message.from_user.id, retry_after)
            try:
                await message.reply_text(
                    f"⚠️ Rate limit exceeded. Please wait {int(retry_after) + 1}s before sending more requests."
                )
            except Exception:
                pass
            return False
    elif message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        group_key = f"group:{message.chat.id}"
        allowed = await group_limiter.acquire(group_key)
        if not allowed:
            retry_after = await group_limiter.get_retry_after(group_key)
            logger.info("Group %d rate limited (retry after %.1fs)", message.chat.id, retry_after)
            return False

    return True
