import os
import time
from pathlib import Path
import pytest
from core.cleaner import check_disk_space, cleanup_directory, purge_old_directories


def test_check_disk_space(tmp_path: Path):
    # Minimum 0.001 GB free should succeed on standard systems
    has_space, free_gb = check_disk_space(tmp_path, min_free_gb=0.001)
    assert has_space is True
    assert free_gb > 0.001

    # Unrealistic high threshold should fail
    has_space_high, _ = check_disk_space(tmp_path, min_free_gb=9999999.0)
    assert has_space_high is False


def test_cleanup_directory(tmp_path: Path):
    target = tmp_path / "temp_job_123"
    target.mkdir()
    sample_file = target / "test.mp4"
    sample_file.write_text("dummy video content")

    assert target.exists()
    cleanup_directory(target)
    assert not target.exists()


def test_purge_old_directories(tmp_path: Path):
    old_dir = tmp_path / "job_old"
    old_dir.mkdir()
    (old_dir / "old.mp4").write_text("old")

    new_dir = tmp_path / "job_new"
    new_dir.mkdir()
    (new_dir / "new.mp4").write_text("new")

    # Manually backdate old_dir mtime by 1 hour (3600 seconds)
    past_time = time.time() - 3600
    os.utime(old_dir, (past_time, past_time))

    # Purge dirs older than 30 minutes
    removed = purge_old_directories(tmp_path, max_age_minutes=30)
    assert removed == 1
    assert not old_dir.exists()
    assert new_dir.exists()
