"""Main application entrypoint, startup/shutdown lifecycles, and Pyrogram MTProto runner."""

import asyncio
import logging
import sys
from pathlib import Path

from pyrogram import Client
from pyrogram.enums import ParseMode

from bot.handlers import (
    register_common_handlers,
    register_group_handlers,
    register_private_handlers,
)
from config import settings
from core.cleaner import purge_old_directories, run_janitor_loop
from core.downloader import DownloaderEngine
from core.queue import ConcurrencyManager, TokenBucketLimiter

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("omniget_bot")


async def main():
    """Initialize bot runtime, MTProto Pyrogram client, background janitor, and long-polling."""
    logger.info("Initializing Telegram Media Downloader Bot with Pyrogram MTProto (2GB limit)...")

    if not settings.BOT_TOKEN:
        logger.critical("BOT_TOKEN is missing! Please configure BOT_TOKEN in .env or environment.")
        sys.exit(1)

    if not settings.API_ID or not settings.API_HASH:
        logger.critical(
            "API_ID and API_HASH are required for MTProto Pyrogram connection. "
            "Please get them from https://my.telegram.org and configure in .env"
        )
        sys.exit(1)

    # Ensure download base folder exists and clean any legacy artifacts on startup
    settings.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stale_cleaned = purge_old_directories(settings.DOWNLOAD_DIR, max_age_minutes=0)
    logger.info("Startup cleaner purged %d leftover temporary directories.", stale_cleaned)

    # Initialize Core Engines
    downloader = DownloaderEngine(base_download_dir=settings.DOWNLOAD_DIR)
    concurrency = ConcurrencyManager(
        max_global=settings.MAX_GLOBAL_CONCURRENT,
        max_user=settings.MAX_USER_CONCURRENT,
        max_group=settings.MAX_GROUP_CONCURRENT,
    )
    user_limiter = TokenBucketLimiter(
        capacity=settings.USER_RATE_LIMIT_PER_MINUTE,
        window_seconds=60.0,
    )
    group_limiter = TokenBucketLimiter(
        capacity=settings.GROUP_RATE_LIMIT_PER_MINUTE,
        window_seconds=60.0,
    )

    # Initialize Pyrogram MTProto Client
    app = Client(
        name=settings.SESSION_NAME,
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        bot_token=settings.BOT_TOKEN,
        parse_mode=ParseMode.MARKDOWN,
        workdir=str(Path.cwd()),
        max_concurrent_transmissions=4,
    )

    # Register Handlers
    register_common_handlers(app)
    register_private_handlers(
        app=app,
        downloader=downloader,
        concurrency=concurrency,
        user_limiter=user_limiter,
        group_limiter=group_limiter,
    )
    register_group_handlers(
        app=app,
        downloader=downloader,
        concurrency=concurrency,
        user_limiter=user_limiter,
        group_limiter=group_limiter,
    )

    # Start Background Janitor Task
    janitor_task = asyncio.create_task(
        run_janitor_loop(
            base_dir=settings.DOWNLOAD_DIR,
            interval_minutes=settings.JANITOR_INTERVAL_MINUTES,
            max_age_minutes=settings.JANITOR_MAX_AGE_MINUTES,
        )
    )

    try:
        await app.start()
        me = await app.get_me()
        logger.info(
            "Bot successfully authenticated via MTProto as @%s (ID: %d)",
            me.username,
            me.id,
        )
        logger.info("Bot is running and ready to handle 2GB media uploads!")

        # Keep client running until interrupted
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received.")
    except Exception as e:
        logger.critical("Fatal runtime error in Pyrogram client: %s", e, exc_info=True)
    finally:
        logger.info("Initiating graceful shutdown...")
        janitor_task.cancel()
        try:
            await janitor_task
        except asyncio.CancelledError:
            pass
        if app.is_connected:
            await app.stop()
        logger.info("Bot shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process terminated by signal.")
