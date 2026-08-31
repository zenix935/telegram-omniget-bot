"""Chat type filters and context routing middlewares."""

from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter
from aiogram.types import Message, TelegramObject


class IsPrivateFilter(BaseFilter):
    """Filter ensuring update is strictly from a private (direct message) chat."""

    async def __call__(self, message: Message) -> bool:
        return message.chat.type == ChatType.PRIVATE


class IsGroupFilter(BaseFilter):
    """Filter ensuring update is from a group or supergroup chat."""

    async def __call__(self, message: Message) -> bool:
        return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


class ChatContextMiddleware(BaseMiddleware):
    """
    Extracts chat metadata (is_group, is_forum, thread_id) and injects into handler kwargs.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            data["is_private"] = event.chat.type == ChatType.PRIVATE
            data["is_group"] = event.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
            data["is_forum"] = bool(event.chat.is_forum)
            data["thread_id"] = event.message_thread_id
        return await handler(event, data)
