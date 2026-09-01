import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.downloader import DownloaderEngine, MediaInfo


@pytest.mark.asyncio
async def test_probe_metadata_success(tmp_path: Path):
    engine = DownloaderEngine(base_download_dir=tmp_path)

    sample_meta = {
        "title": "Amazing Nature Video",
        "extractor_key": "Youtube",
        "duration": 180,
        "filesize_approx": 15000000,
        "thumbnail": "https://example.com/thumb.jpg",
        "is_live": False,
        "ext": "mp4",
    }

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (json.dumps(sample_meta).encode("utf-8"), b"")

    with patch("core.downloader.validate_url", return_value=(True, None)), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        
        success, info, err = await engine.probe_metadata("https://www.youtube.com/watch?v=sample")
        assert success is True
        assert err is None
        assert info is not None
        assert info.title == "Amazing Nature Video"
        assert info.duration == 180
        assert info.extractor == "Youtube"


@pytest.mark.asyncio
async def test_download_disk_limit_rejection(tmp_path: Path):
    engine = DownloaderEngine(base_download_dir=tmp_path)

    # Force check_disk_space to return False
    with patch("core.downloader.validate_url", return_value=(True, None)), \
         patch("core.downloader.check_disk_space", return_value=(False, 1.2)):
        
        success, result, err, temp_dir = await engine.download("https://www.youtube.com/watch?v=sample")
        assert success is False
        assert result is None
        assert "Server disk space low" in err
        assert temp_dir is None


@pytest.mark.asyncio
async def test_download_success(tmp_path: Path):
    engine = DownloaderEngine(base_download_dir=tmp_path)

    # Simulate successful subprocess writing a video file in temp dir
    async def mock_subprocess_exec(*args, **kwargs):
        cwd = Path(kwargs.get("cwd"))
        dummy_file = cwd / "Test_Video [12345].mp4"
        dummy_file.write_bytes(b"x" * 1024 * 50)  # 50 KB dummy file
        
        proc = AsyncMock()
        proc.returncode = 0
        proc.stdout = AsyncMock()
        proc.stdout.readline.side_effect = [b"[download] 100% of 50.00KiB in 00:01\n", b""]
        proc.stderr = AsyncMock()
        proc.stderr.readline.side_effect = [b""]
        proc.wait.return_value = 0
        return proc

    with patch("core.downloader.validate_url", return_value=(True, None)), \
         patch("core.downloader.check_disk_space", return_value=(True, 50.0)), \
         patch("core.downloader.shutil.which", return_value=None), \
         patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess_exec):
        
        progress_called = False
        async def on_progress(pct, line):
            nonlocal progress_called
            progress_called = True

        success, result, err, temp_dir = await engine.download(
            url="https://www.youtube.com/watch?v=12345",
            quality="720p",
            progress_callback=on_progress,
        )

        assert success is True
        assert err is None
        assert result is not None
        assert result.media_type == "video"
        assert result.file_size_bytes == 50 * 1024
        assert temp_dir.exists()

        # Clean up
        from core.cleaner import cleanup_directory
        cleanup_directory(temp_dir)
        assert not temp_dir.exists()
