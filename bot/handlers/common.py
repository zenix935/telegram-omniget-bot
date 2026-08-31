"""Shared error handlers and common commands."""

import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)

router = Router(name="common_router")


@router.message(Command("help"))
async def handle_help_command(message: types.Message):
    """Provide helpful instructions about bot capabilities and supported links."""
    help_text = (
        "🤖 **Telegram Media Downloader Bot**\n\n"
        "Send me any supported media link to download and receive it directly here!\n\n"
        "📌 **Supported Platforms:**\n"
        "• YouTube, Shorts, Music\n"
        "• TikTok, Instagram (Reels & Posts)\n"
        "• Twitter / X, Reddit, Threads\n"
        "• Udemy, Facebook, Vimeo, SoundCloud, Twitch Clips\n\n"
        "⚡ **Features:**\n"
        "• **Direct Messages:** Choose format (Best MP4, 720p, or MP3 Audio).\n"
        "• **Groups & Topics:** Zero-clutter auto-mode that replies in the same topic and cleans status messages.\n"
        "• **Resource Guardrails:** Hard timeouts, disk protection, and rate-limiting.\n\n"
        "⚙️ **Group Admins:** Use `/settings` in your group to toggle auto-download or default quality."
    )
    await message.reply(help_text, parse_mode="Markdown")


@router.error()
async def error_handler(event: ErrorEvent):
    """Global exception handler for unexpected router errors."""
    logger.error("Unhandled exception caught by error handler: %s", event.exception, exc_info=True)
    try:
        if event.update.message:
            await event.update.message.reply(
                "❌ An unexpected error occurred while processing your request. Please try again later."
            )
    except Exception:
        pass
