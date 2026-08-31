"""Disk safety utilities: free disk space check and background janitor task."""

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


def check_disk_space(path: Path, min_free_gb: float = 3.0) -> Tuple[bool, float]:
    """
    Check if the disk holding `path` has at least `min_free_gb` available.

    Returns:
        (is_sufficient: bool, free_gb: float)
    """
    try:
        # If the path doesn't exist yet, check its parent
        target = path
        while not target.exists() and target.parent != target:
            target = target.parent

        usage = shutil.disk_usage(target)
        free_gb = usage.free / (1024**3)
        return free_gb >= min_free_gb, free_gb
    except Exception as e:
        logger.error("Error checking disk space for %s: %s", path, e)
        # Default to safe side if check fails
        return False, 0.0


def cleanup_directory(directory: Path) -> None:
    """Safely remove a directory and all its contents."""
    if not directory.exists():
        return
    try:
        shutil.rmtree(directory, ignore_errors=True)
        logger.debug("Cleaned up directory: %s", directory)
    except Exception as e:
        logger.warning("Failed to clean up directory %s: %s", directory, e)


def purge_old_directories(base_dir: Path, max_age_minutes: int = 30) -> int:
    """
    Purge orphaned directories in base_dir older than max_age_minutes.

    Returns:
        Number of directories removed.
    """
    if not base_dir.exists():
        return 0

    removed_count = 0
    now = time.time()
    max_age_seconds = max_age_minutes * 60

    try:
        for entry in os.scandir(base_dir):
            if entry.is_dir():
                try:
                    stat_info = entry.stat()
                    dir_age = now - stat_info.st_mtime
                    if dir_age > max_age_seconds:
                        logger.info(
                            "Janitor purging old directory: %s (age: %.1f mins)",
                            entry.path,
                            dir_age / 60,
                        )
                        shutil.rmtree(entry.path, ignore_errors=True)
                        removed_count += 1
                except Exception as e:
                    logger.warning("Error inspecting/removing %s: %s", entry.path, e)
    except Exception as e:
        logger.error("Error scanning base directory %s: %s", base_dir, e)

    return removed_count


async def run_janitor_loop(
    base_dir: Path,
    interval_minutes: int = 15,
    max_age_minutes: int = 30,
) -> None:
    """Background periodic async task that cleans up orphaned files and folders."""
    logger.info(
        "Starting Janitor background loop. Interval: %dm, Max age: %dm, Target: %s",
        interval_minutes,
        max_age_minutes,
        base_dir,
    )
    base_dir.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            await asyncio.sleep(interval_minutes * 60)
            removed = purge_old_directories(base_dir, max_age_minutes=max_age_minutes)
            if removed > 0:
                logger.info("Janitor cycle complete: removed %d stale directories", removed)
        except asyncio.CancelledError:
            logger.info("Janitor task received cancellation, stopping.")
            break
        except Exception as e:
            logger.error("Unexpected error in Janitor loop: %s", e, exc_info=True)
