import pytest
from aiogram.enums import MessageEntityType
from aiogram.types import Message, MessageEntity, User, Chat
from bot.utils.helpers import extract_links_from_message, format_bytes, format_duration, format_progress_bar


def test_format_helpers():
    assert format_bytes(500) == "500.0 B"
    assert format_bytes(1024 * 1024 * 15) == "15.0 MB"
    assert format_duration(65) == "01:05"
    assert format_duration(3665) == "01:01:05"
    assert "50.0%" in format_progress_bar(50.0)


def test_extract_links_entities():
    # Simulated message with URL entity
    text = "Check this cool video https://www.youtube.com/watch?v=abc and discuss!"
    # offset of https... is 22, length is 39
    url_start = text.index("https://")
    url_str = "https://www.youtube.com/watch?v=abc"
    entity = MessageEntity(type=MessageEntityType.URL, offset=url_start, length=len(url_str))

    chat = Chat(id=123, type="group")
    user = User(id=456, is_bot=False, first_name="Test")
    msg = Message(
        message_id=1,
        date=123456789,
        chat=chat,
        from_user=user,
        text=text,
        entities=[entity],
    )

    links = extract_links_from_message(msg, max_links=2)
    assert len(links) == 1
    assert links[0] == url_str


def test_extract_links_regex_fallback():
    text = "Direct raw link: https://tiktok.com/@creator/video/987654 inside text"
    chat = Chat(id=123, type="private")
    user = User(id=456, is_bot=False, first_name="Test")
    msg = Message(
        message_id=1,
        date=123456789,
        chat=chat,
        from_user=user,
        text=text,
        entities=None,
    )

    links = extract_links_from_message(msg, max_links=2)
    assert len(links) == 1
    assert "tiktok.com" in links[0]
