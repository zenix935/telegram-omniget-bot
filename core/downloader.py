"""Asynchronous wrapper for omniget-cli & yt-dlp with hard timeouts, process sandboxing, and progress parsing."""

import asyncio
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Coroutine, Dict, List, Optional, Tuple, Any

from config import settings
from core.cleaner import check_disk_space, cleanup_directory
from core.security import validate_url

logger = logging.getLogger(__name__)


@dataclass
class MediaInfo:
    """Metadata extracted for a given URL."""
    url: str
    title: str
    extractor: str
    duration: Optional[int] = None
    filesize_approx: Optional[int] = None
    thumbnail_url: Optional[str] = None
    is_live: bool = False
    formats_available: Optional[List[Dict[str, Any]]] = None
    description: Optional[str] = None
    ext: str = "mp4"


@dataclass
class DownloadResult:
    """Result of a download job."""
    temp_dir: Path
    file_path: Path
    filename: str
    file_size_bytes: int
    title: str
    duration: Optional[int] = None
    thumbnail_path: Optional[Path] = None
    media_type: str = "video"  # "video", "audio", "document"
    width: Optional[int] = None
    height: Optional[int] = None


class DownloaderEngine:
    """
    Subprocess-safe downloader handling omniget-cli and yt-dlp engines.
    Ensures safe arguments (no shell=True), isolated UUID directories, and guaranteed cleanup.
    """

    def __init__(self, base_download_dir: Optional[Path] = None):
        self.base_download_dir = base_download_dir or settings.DOWNLOAD_DIR
        self.base_download_dir.mkdir(parents=True, exist_ok=True)

    async def probe_metadata(self, url: str) -> Tuple[bool, Optional[MediaInfo], Optional[str]]:
        """
        Probe URL metadata safely using yt-dlp in JSON mode without downloading the media.
        """
        # SSRF & syntax check
        valid, reason = validate_url(url)
        if not valid:
            return False, None, reason or "Invalid URL"

        cmd = [
            settings.YTDLP_BIN,
            "--dump-single-json",
            "--no-playlist",
            "--no-warnings",
            "--no-check-certificates",
            "--skip-download",
            "--socket-timeout", "15",
            "--",
            url,
        ]

        logger.debug("Probing metadata with cmd: %s", cmd)
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=30.0,
            )

            if process.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.warning("Metadata probe failed for %s: %s", url, err_msg)
                # Shorten error message for user
                user_err = err_msg.splitlines()[-1] if err_msg else "Could not extract metadata"
                return False, None, user_err

            info_dict = json.loads(stdout.decode("utf-8", errors="replace"))

            title = info_dict.get("title") or "Unknown Title"
            duration = info_dict.get("duration")
            extractor = info_dict.get("extractor_key") or info_dict.get("extractor") or "generic"
            filesize = info_dict.get("filesize") or info_dict.get("filesize_approx")
            thumbnail = info_dict.get("thumbnail")
            is_live = bool(info_dict.get("is_live", False))
            ext = info_dict.get("ext") or "mp4"

            media_info = MediaInfo(
                url=url,
                title=title,
                extractor=extractor,
                duration=duration,
                filesize_approx=filesize,
                thumbnail_url=thumbnail,
                is_live=is_live,
                formats_available=info_dict.get("formats"),
                description=info_dict.get("description"),
                ext=ext,
            )
            return True, media_info, None

        except asyncio.TimeoutError:
            return False, None, "Timeout while probing URL metadata (site did not respond in time)."
        except Exception as e:
            logger.error("Exception during probe_metadata for %s: %s", url, e, exc_info=True)
            return False, None, f"Metadata probe error: {str(e)}"

    async def download(
        self,
        url: str,
        quality: str = "best",  # "best", "720p", "audio", or "mp3"
        progress_callback: Optional[Callable[[float, str], Coroutine[Any, Any, None]]] = None,
    ) -> Tuple[bool, Optional[DownloadResult], Optional[str], Optional[Path]]:
        """
        Download media into an isolated UUID directory.

        Returns:
            (success: bool, result: Optional[DownloadResult], error_message: Optional[str], temp_dir: Optional[Path])
        """
        # 1. Pre-download URL validation
        valid, reason = validate_url(url)
        if not valid:
            return False, None, reason or "Invalid URL", None

        # 2. Pre-download disk check
        has_space, free_gb = check_disk_space(self.base_download_dir, min_free_gb=settings.MIN_FREE_DISK_GB)
        if not has_space:
            return (
                False,
                None,
                f"Server disk space low ({free_gb:.2f} GB free, minimum required is {settings.MIN_FREE_DISK_GB} GB). Please try again later.",
                None,
            )

        # 3. Create isolated UUID workspace
        job_id = uuid.uuid4().hex
        temp_dir = self.base_download_dir / f"job_{job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 4. Determine engine & format arguments
            use_omniget = bool(settings.OMNIGET_BIN and shutil.which(settings.OMNIGET_BIN))
            
            if use_omniget:
                cmd = self._build_omniget_cmd(url, temp_dir, quality)
            else:
                cmd = self._build_ytdlp_cmd(url, temp_dir, quality)

            logger.info("Executing download command: %s", " ".join(cmd))

            # 5. Spawn subprocess with timeout & sanitized exec (NEVER shell=True)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(temp_dir),
            )

            # Progress tracking regexes
            pct_regex = re.compile(r"(\d+(?:\.\d+)?)%")
            last_progress_time = 0.0

            async def read_stream():
                nonlocal last_progress_time
                assert process.stdout is not None
                while True:
                    line_bytes = await process.stdout.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    # yt-dlp progress line example: [download]  42.5% of ~ 15.20MiB at 3.12MiB/s ETA 00:02
                    if "[download]" in line and "%" in line:
                        match = pct_regex.search(line)
                        if match:
                            try:
                                pct = float(match.group(1))
                                if progress_callback:
                                    await progress_callback(pct, line)
                            except Exception:
                                pass

            # Run stream reader and await process with hard timeout
            try:
                reader_task = asyncio.create_task(read_stream())
                
                async def wait_process():
                    stdout, stderr = await process.communicate()
                    return process.returncode, stderr

                _, (returncode, stderr) = await asyncio.gather(
                    reader_task,
                    asyncio.wait_for(wait_process(), timeout=float(settings.DOWNLOAD_TIMEOUT_SECONDS)),
                )

            except asyncio.TimeoutError:
                logger.warning("Download process timed out after %ds for %s", settings.DOWNLOAD_TIMEOUT_SECONDS, url)
                try:
                    process.terminate()
                    await asyncio.sleep(1)
                    if process.returncode is None:
                        process.kill()
                except Exception:
                    pass
                return False, None, f"Download timed out after {settings.DOWNLOAD_TIMEOUT_SECONDS}s", temp_dir

            if returncode != 0:
                err_str = stderr.decode("utf-8", errors="replace").strip()
                logger.warning("Download subprocess failed with exit code %d: %s", returncode, err_str)
                user_err = err_str.splitlines()[-1] if err_str else "Download process failed."
                return False, None, user_err, temp_dir

            # 6. Locate downloaded output file in temp_dir
            downloaded_files = [
                f for f in temp_dir.iterdir()
                if f.is_file() and not f.name.endswith((".temp", ".part", ".ytdl", ".aria2"))
            ]

            if not downloaded_files:
                return False, None, "No output file found after download completed.", temp_dir

            # Find media file vs thumbnail
            media_files = [f for f in downloaded_files if not f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
            thumbnails = [f for f in downloaded_files if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]

            target_file = media_files[0] if media_files else downloaded_files[0]
            thumb_path = thumbnails[0] if thumbnails else None
            file_size = target_file.stat().st_size

            # Check max file size against Bot API limits
            max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
            if file_size > max_bytes:
                size_mb = file_size / (1024 * 1024)
                return (
                    False,
                    None,
                    f"Downloaded file size ({size_mb:.1f} MB) exceeds Telegram Bot API limit ({settings.MAX_FILE_SIZE_MB} MB).",
                    temp_dir,
                )

            # Determine media type
            ext = target_file.suffix.lower()
            if ext in (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav"):
                media_type = "audio"
            elif ext in (".mp4", ".mkv", ".webm", ".mov", ".avi"):
                media_type = "video"
            else:
                media_type = "document"

            # Probe video metadata using ffprobe if available
            duration = None
            width = None
            height = None
            video_meta = await self._probe_file_streams(target_file)
            if video_meta:
                duration = video_meta.get("duration")
                width = video_meta.get("width")
                height = video_meta.get("height")

            result = DownloadResult(
                temp_dir=temp_dir,
                file_path=target_file,
                filename=target_file.name,
                file_size_bytes=file_size,
                title=target_file.stem,
                duration=duration,
                thumbnail_path=thumb_path,
                media_type=media_type,
                width=width,
                height=height,
            )

            return True, result, None, temp_dir

        except Exception as e:
            logger.error("Download execution error for %s: %s", url, e, exc_info=True)
            return False, None, f"Unexpected error during download: {str(e)}", temp_dir

    def _build_ytdlp_cmd(self, url: str, output_dir: Path, quality: str) -> List[str]:
        """Build sanitized yt-dlp arguments with strict thread and safety caps."""
        out_template = str(output_dir / "%(title).100s [%(id)s].%(ext)s")
        
        cmd = [
            settings.YTDLP_BIN,
            "--no-playlist",
            "--no-warnings",
            "--no-check-certificates",
            "--socket-timeout", "20",
            "--retries", "3",
            "--output", out_template,
            "--write-thumbnail",
            "--convert-thumbnails", "jpg",
            # Strict FFmpeg thread limiter to prevent CPU starvation
            "--postprocessor-args", f"ffmpeg:-threads {settings.FFMPEG_THREADS}",
        ]

        if quality in ("audio", "mp3"):
            cmd.extend([
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "0",
            ])
        elif quality == "720p":
            cmd.extend([
                "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
                "--merge-output-format", "mp4",
            ])
        else:  # "best" or default
            cmd.extend([
                "-f", "bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
            ])

        cmd.extend(["--", url])
        return cmd

    def _build_omniget_cmd(self, url: str, output_dir: Path, quality: str) -> List[str]:
        """Build omniget-cli arguments if installed."""
        cmd = [
            settings.OMNIGET_BIN or "omniget-cli",
            "download",
            "--output-dir", str(output_dir),
        ]
        if quality in ("audio", "mp3"):
            cmd.extend(["--format", "audio"])
        elif quality == "720p":
            cmd.extend(["--resolution", "720p"])

        cmd.extend(["--", url])
        return cmd

    async def _probe_file_streams(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Probe local video dimensions & duration using ffprobe if available."""
        if not shutil.which("ffprobe"):
            return None

        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "stream=width,height,duration:format=duration",
            "-of", "json",
            str(file_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode == 0:
                data = json.loads(stdout.decode("utf-8"))
                duration_val = None
                if "format" in data and "duration" in data["format"]:
                    duration_val = int(float(data["format"]["duration"]))

                width_val = None
                height_val = None
                if "streams" in data:
                    for s in data["streams"]:
                        if "width" in s and "height" in s:
                            width_val = int(s["width"])
                            height_val = int(s["height"])
                            break
                return {
                    "duration": duration_val,
                    "width": width_val,
                    "height": height_val,
                }
        except Exception:
            pass
        return None
