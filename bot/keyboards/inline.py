"""Inline keyboard generators for private chat format selection and settings in Pyrogram."""

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def make_format_selector_keyboard(download_key: str) -> InlineKeyboardMarkup:
    """
    Build interactive inline buttons for private chat media selection using Pyrogram types.
    download_key is an identifier to look up queued download info in state/cache.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="📹 Best Quality",
                    callback_data=f"dl:best:{download_key}",
                ),
                InlineKeyboardButton(
                    text="📹 720p MP4",
                    callback_data=f"dl:720p:{download_key}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎵 MP3 Audio",
                    callback_data=f"dl:audio:{download_key}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"dl:cancel:{download_key}",
                ),
            ],
        ]
    )


def make_group_settings_keyboard(enabled: bool, default_quality: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for group admin configuration using Pyrogram types."""
    toggle_text = "🟢 Auto-Download: Enabled" if enabled else "🔴 Auto-Download: Disabled"
    qual_720_mark = "✓ 720p" if default_quality == "720p" else "720p"
    qual_best_mark = "✓ Best" if default_quality == "best" else "Best"
    qual_audio_mark = "✓ Audio" if default_quality == "audio" else "Audio"

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data="grp_set:toggle",
                )
            ],
            [
                InlineKeyboardButton(text=qual_720_mark, callback_data="grp_set:qual:720p"),
                InlineKeyboardButton(text=qual_best_mark, callback_data="grp_set:qual:best"),
                InlineKeyboardButton(text=qual_audio_mark, callback_data="grp_set:qual:audio"),
            ],
            [
                InlineKeyboardButton(text="Close Settings", callback_data="grp_set:close")
            ],
        ]
    )
