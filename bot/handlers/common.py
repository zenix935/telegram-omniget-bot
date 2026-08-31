"""Shared commands and global error handlers for Pyrogram."""

import logging
from pyrogram import Client, filters
from pyrogram.types import Message

logger = logging.getLogger(__name__)


def register_common_handlers(app: Client):
    """Register common commands like /help and /start redirection."""

    @app.on_message(filters.command(["help"]))
    async def handle_help_command(client: Client, message: Message):
        """Provide instructions about bot capabilities and supported links."""
        help_text = (
            "🤖 **Telegram Media Downloader Bot (MTProto 2GB)**\n\n"
            "Send me any supported media link to download and receive it directly here!\n\n"
            "📌 **Supported Platforms:**\n"
            "• YouTube, Shorts, Music\n"
            "• TikTok, Instagram (Reels & Posts)\n"
            "• Twitter / X, Reddit, Threads\n"
            "• Udemy, Facebook, Vimeo, SoundCloud, Twitch Clips\n\n"
            "⚡ **Features:**\n"
            "• **MTProto 2GB Uploads:** Natively upload large files up to 2GB.\n"
            "• **Direct Messages:** Interactive buttons (Best MP4, 720p, MP3 Audio).\n"
            "• **Groups & Topics:** Zero-clutter auto-mode that replies in the same topic and cleans status messages.\n"
            "• **Resource Guardrails:** Hard timeouts, disk protection, and rate-limiting.\n\n"
            "⚙️ **Group Admins:** Use `/settings` in your group to toggle auto-download or default quality."
        )
        await message.reply_text(help_text)
