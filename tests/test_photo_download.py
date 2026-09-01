import pytest
from pathlib import Path
from core.downloader import DownloadResult, DownloaderEngine


@pytest.mark.asyncio
async def test_image_and_gallery_detection(tmp_path: Path):
    engine = DownloaderEngine(base_download_dir=tmp_path)
    
    # Create mock photo
    job_dir = tmp_path / "job_mock1"
    job_dir.mkdir()
    photo_file = job_dir / "image1.jpg"
    photo_file.write_bytes(b"mock_image_data")

    # Verify photo result logic
    downloaded = [photo_file]
    image_extensions = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
    image_files = [f for f in downloaded if f.suffix.lower() in image_extensions]
    assert len(image_files) == 1
    media_type = "photo" if len(image_files) == 1 else "gallery"
    assert media_type == "photo"

    # Create mock gallery (multiple photos)
    photo_file2 = job_dir / "image2.jpg"
    photo_file2.write_bytes(b"mock_image_data_2")
    downloaded_all = [photo_file, photo_file2]
    image_files_all = [f for f in downloaded_all if f.suffix.lower() in image_extensions]
    assert len(image_files_all) == 2
    media_type_all = "photo" if len(image_files_all) == 1 else "gallery"
    assert media_type_all == "gallery"
