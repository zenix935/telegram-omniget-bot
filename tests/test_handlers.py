from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiogram.enums import ChatType
from aiogram.types import Chat, Message, User

from bot.handlers.group import get_group_config, is_user_group_admin
from bot.handlers.private import PENDING_DOWNLOADS
from config import settings
from core.downloader import MediaInfo


@pytest.mark.asyncio
async def test_group_config():
    cfg = get_group_config(-100999)
    assert cfg["auto_download"] is True
    assert cfg["quality"] == "best"


@pytest.mark.asyncio
async def test_is_user_group_admin():
    bot = AsyncMock()
    member = MagicMock()
    member.status = "administrator"
    bot.get_chat_member.return_value = member

    is_admin = await is_user_group_admin(bot, chat_id=-100123, user_id=456)
    assert is_admin is True

    member.status = "member"
    bot.get_chat_member.return_value = member
    is_admin_member = await is_user_group_admin(bot, chat_id=-100123, user_id=789)
    assert is_admin_member is False


@pytest.mark.asyncio
async def test_pending_downloads_lifecycle():
    sample_key = "test_key_123"
    info = MediaInfo(url="https://youtube.com/watch?v=1", title="Test Video", extractor="youtube")
    PENDING_DOWNLOADS[sample_key] = {
        "url": "https://youtube.com/watch?v=1",
        "user_id": 999,
        "created_at": 1000.0,
        "media_info": info,
    }
    assert sample_key in PENDING_DOWNLOADS
    assert PENDING_DOWNLOADS[sample_key]["user_id"] == 999
    PENDING_DOWNLOADS.pop(sample_key, None)
    assert sample_key not in PENDING_DOWNLOADS
