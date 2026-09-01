"""Inline keyboard generators for private chat format selection and settings."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def make_format_selector_keyboard(download_key: str) -> InlineKeyboardMarkup:
    """
    Build interactive inline buttons for private chat media selection.
    download_key is an identifier to look up queued download info in state/cache.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📹 Best Quality",
            callback_data=f"dl:best:{download_key}",
        ),
        InlineKeyboardButton(
            text="📹 720p MP4",
            callback_data=f"dl:720p:{download_key}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🎵 MP3 Audio",
            callback_data=f"dl:audio:{download_key}",
        ),
        InlineKeyboardButton(
            text="❌ Cancel",
            callback_data=f"dl:cancel:{download_key}",
        ),
    )
    return builder.as_markup()


def make_group_settings_keyboard(enabled: bool, default_quality: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for group admin configuration."""
    builder = InlineKeyboardBuilder()
    
    toggle_text = "🟢 Auto-Download: Enabled" if enabled else "🔴 Auto-Download: Disabled"
    builder.row(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data="grp_set:toggle",
        )
    )
    
    qual_720_mark = "✓ 720p" if default_quality == "720p" else "720p"
    qual_best_mark = "✓ Best" if default_quality == "best" else "Best"
    qual_audio_mark = "✓ Audio" if default_quality == "audio" else "Audio"

    builder.row(
        InlineKeyboardButton(text=qual_720_mark, callback_data="grp_set:qual:720p"),
        InlineKeyboardButton(text=qual_best_mark, callback_data="grp_set:qual:best"),
        InlineKeyboardButton(text=qual_audio_mark, callback_data="grp_set:qual:audio"),
    )
    builder.row(
        InlineKeyboardButton(text="Close Settings", callback_data="grp_set:close")
    )
    return builder.as_markup()


def make_admin_panel_keyboard() -> InlineKeyboardMarkup:
    """Build interactive inline keyboard for admin panel control."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Refresh", callback_data="adm_panel:refresh"),
        InlineKeyboardButton(text="🏆 Top Users", callback_data="adm_panel:top"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Close", callback_data="adm_panel:close"),
    )
    return builder.as_markup()
