"""Main application entrypoint, startup/shutdown lifecycles, and polling manager."""

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode

from bot.handlers import common_router, group_router, private_router
from bot.middlewares import ChatContextMiddleware, RateLimitMiddleware
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
    """Initialize bot runtime, core engines, background janitor, and polling loops."""
    logger.info("Initializing Telegram Media Downloader Bot...")

    if not settings.BOT_TOKEN:
        logger.critical("BOT_TOKEN is missing! Please configure BOT_TOKEN in .env or environment.")
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

    # Initialize Bot Session (support local Bot API server if configured)
    session = None
    if settings.BOT_API_SERVER:
        logger.info("Using custom Telegram Bot API server: %s", settings.BOT_API_SERVER)
        api_server = TelegramAPIServer.from_base(settings.BOT_API_SERVER, is_local=True)
        session = AiohttpSession(api=api_server)

    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Initialize Dispatcher
    dp = Dispatcher()

    # Register Middlewares
    dp.message.middleware(ChatContextMiddleware())
    dp.message.middleware(RateLimitMiddleware(user_limiter=user_limiter, group_limiter=group_limiter))

    # Register Routers (order matters: common commands -> group / private)
    dp.include_router(common_router)
    dp.include_router(private_router)
    dp.include_router(group_router)

    # Dependency Injection into handlers
    dp["downloader"] = downloader
    dp["concurrency"] = concurrency

    # Start Background Janitor Task
    janitor_task = asyncio.create_task(
        run_janitor_loop(
            base_dir=settings.DOWNLOAD_DIR,
            interval_minutes=settings.JANITOR_INTERVAL_MINUTES,
            max_age_minutes=settings.JANITOR_MAX_AGE_MINUTES,
        )
    )

    # Start Polling with clean shutdown lifecycle
    try:
        me = await bot.get_me()
        logger.info("Bot successfully authenticated as @%s (ID: %d)", me.username, me.id)
        logger.info("Starting update polling loop...")
        
        # Drop pending updates on startup to prevent flooding from historical group messages
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical("Fatal runtime error in bot polling: %s", e, exc_info=True)
    finally:
        logger.info("Initiating graceful shutdown...")
        janitor_task.cancel()
        try:
            await janitor_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("Bot shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process terminated by signal.")
