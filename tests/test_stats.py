import pytest
import time
from pathlib import Path
from core.stats import StatsTracker


@pytest.mark.asyncio
async def test_stats_tracker(tmp_path: Path):
    db_file = tmp_path / "test_stats.db"
    tracker = StatsTracker(db_path=db_file)

    # Initially empty
    stats = await tracker.get_all_stats()
    assert stats["1d"]["total_bytes"] == 0
    assert stats["1d"]["total_count"] == 0
    assert stats["all"]["total_bytes"] == 0

    # Record downloads
    await tracker.record_download(
        user_id=1001,
        chat_id=1001,
        is_group=False,
        file_size_bytes=50 * 1024 * 1024,
        media_type="video",
        quality="best",
    )
    await tracker.record_download(
        user_id=1002,
        chat_id=-100500,
        is_group=True,
        file_size_bytes=100 * 1024 * 1024,
        media_type="video",
        quality="720p",
    )

    stats = await tracker.get_all_stats()
    assert stats["1d"]["total_bytes"] == 150 * 1024 * 1024
    assert stats["1d"]["total_count"] == 2
    assert stats["1d"]["unique_users"] == 2
    assert stats["7d"]["total_bytes"] == 150 * 1024 * 1024
    assert stats["30d"]["total_bytes"] == 150 * 1024 * 1024
    assert stats["all"]["total_bytes"] == 150 * 1024 * 1024

    # Top users
    top_users = await tracker.get_top_users(limit=5)
    assert len(top_users) == 2
    assert top_users[0][0] == 1002
    assert top_users[0][1] == 100 * 1024 * 1024
    assert top_users[1][0] == 1001
