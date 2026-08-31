"""Rate limiting middleware for incoming messages."""

import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject

from config import settings
from core.queue import TokenBucketLimiter

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """
    Applies sliding window rate limiting for both private users and groups.
    """

    def __init__(
        self,
        user_limiter: TokenBucketLimiter,
        group_limiter: TokenBucketLimiter,
    ):
        self.user_limiter = user_limiter
        self.group_limiter = group_limiter

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.from_user:
            return await handler(event, data)

        # Skip rate limit for global admins
        if event.from_user.id in settings.ADMIN_IDS:
            return await handler(event, data)

        # Check chat type
        if event.chat.type == ChatType.PRIVATE:
            user_key = f"user:{event.from_user.id}"
            allowed = await self.user_limiter.acquire(user_key)
            if not allowed:
                retry_after = await self.user_limiter.get_retry_after(user_key)
                logger.info("User %d rate limited in DM (retry after %.1fs)", event.from_user.id, retry_after)
                try:
                    await event.reply(
                        f"⚠️ Rate limit exceeded. Please wait {int(retry_after) + 1}s before sending more requests."
                    )
                except Exception:
                    pass
                return None
        elif event.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            group_key = f"group:{event.chat.id}"
            allowed = await self.group_limiter.acquire(group_key)
            if not allowed:
                retry_after = await self.group_limiter.get_retry_after(group_key)
                logger.info("Group %d rate limited (retry after %.1fs)", event.chat.id, retry_after)
                # In groups we log and silently drop or reply lightly to prevent spam cascades
                return None

        return await handler(event, data)
