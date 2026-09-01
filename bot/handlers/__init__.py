from bot.handlers.admin import router as admin_router
from bot.handlers.common import router as common_router
from bot.handlers.group import router as group_router
from bot.handlers.private import router as private_router

__all__ = ["admin_router", "common_router", "group_router", "private_router"]
