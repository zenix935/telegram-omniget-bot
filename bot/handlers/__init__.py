from bot.handlers.common import register_common_handlers
from bot.handlers.group import register_group_handlers
from bot.handlers.private import register_private_handlers

__all__ = [
    "register_common_handlers",
    "register_group_handlers",
    "register_private_handlers",
]
