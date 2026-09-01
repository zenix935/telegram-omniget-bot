"""Asynchronous wrapper for omniget-cli & yt-dlp with hard timeouts, process sandboxing, and progress parsing."""

import asyncio
import json
import logging
import os
import re
import shutil
import uuid
import aiohttp
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Coroutine, Dict, List, Optional, Tuple, Any

import yt_dlp
from yt_dlp.extractor.instagram import InstagramIE

from config import settings
from core.cleaner import check_disk_space, cleanup_directory
from core.security import validate_url

logger = logging.getLogger(__name__)


class PhotoFriendlyInstagramIE(InstagramIE):
    """Instagram Extractor that doesn't raise error on photo-only posts."""
    def raise_no_formats(self, msg="No video formats found!", expected=False, video_id=None):
        return


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
    is_photo: bool = False


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
    media_type: str = "video"  # "video", "audio", "photo", "gallery", "document"
    width: Optional[int] = None
    height: Optional[int] = None
    extra_files: Optional[List[Path]] = None


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
        Probe URL metadata safely using yt-dlp/gallery-dl in JSON mode without downloading the media.
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
            # First check if it's an Instagram URL
            if "instagram.com" in url:
                try:
                    def _extract_ig():
                        ydl = yt_dlp.YoutubeDL({
                            "skip_download": True,
                            "ignoreerrors": True,
                            "no_warnings": True,
                            "quiet": True,
                        })
                        ie = PhotoFriendlyInstagramIE(ydl)
                        return ie._real_extract(url)

                    info_dict = await asyncio.to_thread(_extract_ig)
                    if info_dict:
                        title = info_dict.get("title") or info_dict.get("description") or "Instagram Post"
                        if len(title) > 80:
                            title = title[:80] + "..."
                        duration = info_dict.get("duration")
                        extractor = "Instagram"
                        formats = info_dict.get("formats") or []
                        is_video = any(f.get("vcodec") != "none" for f in formats) or info_dict.get("ext") in ("mp4", "webm")
                        thumbs = info_dict.get("thumbnails") or []
                        thumb = thumbs[-1]["url"] if thumbs else None
                        
                        media_info = MediaInfo(
                            url=url,
                            title=title,
                            extractor=extractor,
                            duration=duration,
                            filesize_approx=None,
                            thumbnail_url=thumb,
                            is_live=False,
                            formats_available=formats if is_video else None,
                            description=info_dict.get("description"),
                            ext="mp4" if is_video else "jpg",
                            is_photo=not is_video,
                        )
                        return True, media_info, None
                except Exception as ig_err:
                    logger.debug("Instagram photo extractor probe error: %s", ig_err)

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=30.0,
            )

            if process.returncode == 0:
                info_dict = json.loads(stdout.decode("utf-8", errors="replace"))

                title = info_dict.get("title") or "Unknown Title"
                duration = info_dict.get("duration")
                extractor = info_dict.get("extractor_key") or info_dict.get("extractor") or "generic"
                filesize = info_dict.get("filesize") or info_dict.get("filesize_approx")
                thumbnail = info_dict.get("thumbnail")
                is_live = bool(info_dict.get("is_live", False))
                ext = info_dict.get("ext") or "mp4"
                formats = info_dict.get("formats") or []
                is_photo = ext in ("jpg", "jpeg", "png", "webp") or not formats

                media_info = MediaInfo(
                    url=url,
                    title=title,
                    extractor=extractor,
                    duration=duration,
                    filesize_approx=filesize,
                    thumbnail_url=thumbnail,
                    is_live=is_live,
                    formats_available=formats,
                    description=info_dict.get("description"),
                    ext=ext,
                    is_photo=is_photo,
                )
                return True, media_info, None

            # If yt-dlp metadata probe fails, attempt probe via gallery-dl for image/photo sites
            if shutil.which(settings.GALLERYDL_BIN):
                gdl_cmd = [settings.GALLERYDL_BIN, "-j", url]
                try:
                    gdl_proc = await asyncio.create_subprocess_exec(
                        *gdl_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    gdl_out, gdl_err = await asyncio.wait_for(gdl_proc.communicate(), timeout=15.0)
                    if gdl_proc.returncode == 0 and gdl_out:
                        data = json.loads(gdl_out.decode("utf-8", errors="replace"))
                        if isinstance(data, list) and data:
                            entry = data[0]
                            # gallery-dl JSON format is list of [[type_code, dict]] or [dict]
                            item_dict = entry[1] if isinstance(entry, list) and len(entry) > 1 and isinstance(entry[1], dict) else (entry if isinstance(entry, dict) else {})
                            title = item_dict.get("title") or item_dict.get("description") or "Image / Photo Post"
                            if len(title) > 80:
                                title = title[:80] + "..."
                            extractor = item_dict.get("category") or "gallery"
                            media_info = MediaInfo(
                                url=url,
                                title=title,
                                extractor=extractor,
                                duration=None,
                                filesize_approx=None,
                                thumbnail_url=None,
                                is_live=False,
                                formats_available=None,
                                description=None,
                                ext="jpg",
                            )
                            return True, media_info, None
                except Exception as ge:
                    logger.debug("gallery-dl probe error: %s", ge)

            err_msg = stderr.decode("utf-8", errors="replace").strip()
            logger.warning("Metadata probe failed for %s: %s", url, err_msg)
            user_err = err_msg.splitlines()[-1] if err_msg else "Could not extract metadata"
            return False, None, user_err

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
            # Use 10MB limit for stdout/stderr streams to prevent LimitOverrunError on large progress lines
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=10 * 1024 * 1024,
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
                async def read_stream():
                    assert process.stdout is not None
                    while True:
                        try:
                            line_bytes = await process.stdout.readline()
                        except ValueError:
                            # Handle rare chunk overrun without newline (e.g. carriage-return progress overwrites)
                            line_bytes = await process.stdout.read(65536)
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

                async def read_stderr():
                    assert process.stderr is not None
                    err_lines = []
                    while True:
                        try:
                            line_bytes = await process.stderr.readline()
                        except ValueError:
                            line_bytes = await process.stderr.read(65536)
                        if not line_bytes:
                            break
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        if line:
                            err_lines.append(line)
                    return "\n".join(err_lines).encode("utf-8")

                async def wait_process():
                    reader_task = asyncio.create_task(read_stream())
                    err_task = asyncio.create_task(read_stderr())
                    await reader_task
                    stderr_bytes = await err_task
                    returncode = await process.wait()
                    return returncode, stderr_bytes

                returncode, stderr = await asyncio.wait_for(
                    wait_process(), timeout=float(settings.DOWNLOAD_TIMEOUT_SECONDS)
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

            # If it's an Instagram post, download images directly using high-res thumbnails if not video
            if "instagram.com" in url:
                try:
                    def _extract_ig_download():
                        ydl = yt_dlp.YoutubeDL({
                            "skip_download": True,
                            "ignoreerrors": True,
                            "no_warnings": True,
                            "quiet": True,
                        })
                        ie = PhotoFriendlyInstagramIE(ydl)
                        return ie._real_extract(url)

                    ig_info = await asyncio.to_thread(_extract_ig_download)
                    if ig_info:
                        formats = ig_info.get("formats") or []
                        is_video = any(f.get("vcodec") != "none" for f in formats) or ig_info.get("ext") in ("mp4", "webm")
                        if not is_video:
                            # Download photo(s)
                            entries = ig_info.get("entries", [])
                            title = ig_info.get("title") or "Instagram Photo"
                            img_files = []
                            async with aiohttp.ClientSession() as session:
                                if entries:
                                    for idx, entry in enumerate(entries):
                                        if not entry:
                                            continue
                                        thumbs = entry.get("thumbnails", [])
                                        if thumbs:
                                            best_url = thumbs[-1]["url"]
                                            dest = temp_dir / f"ig_{ig_info.get('id', 'post')}_{idx+1}.jpg"
                                            async with session.get(best_url) as resp:
                                                if resp.status == 200:
                                                    dest.write_bytes(await resp.read())
                                                    img_files.append(dest)
                                else:
                                    thumbs = ig_info.get("thumbnails", [])
                                    if thumbs:
                                        best_url = thumbs[-1]["url"]
                                        dest = temp_dir / f"ig_{ig_info.get('id', 'post')}.jpg"
                                        async with session.get(best_url) as resp:
                                            if resp.status == 200:
                                                dest.write_bytes(await resp.read())
                                                img_files.append(dest)

                            if img_files:
                                target_file = img_files[0]
                                media_type = "photo" if len(img_files) == 1 else "gallery"
                                total_size = sum(f.stat().st_size for f in img_files)
                                result = DownloadResult(
                                    temp_dir=temp_dir,
                                    file_path=target_file,
                                    filename=target_file.name,
                                    file_size_bytes=total_size,
                                    title=title,
                                    duration=None,
                                    thumbnail_path=None,
                                    media_type=media_type,
                                    width=None,
                                    height=None,
                                    extra_files=img_files[1:],
                                )
                                return True, result, None, temp_dir
                except Exception as ig_err:
                    logger.warning("Direct Instagram photo download error: %s", ig_err)

            # 6. Locate downloaded output file in temp_dir
            downloaded_files = [
                f for f in temp_dir.rglob("*")
                if f.is_file() and not f.name.endswith((".temp", ".part", ".ytdl", ".aria2"))
            ]

            # If yt-dlp produced no files (e.g. image-only / photo post) and gallery-dl is available, try gallery-dl fallback
            if not downloaded_files and shutil.which(settings.GALLERYDL_BIN):
                logger.info("yt-dlp returned no media file, trying gallery-dl fallback for %s", url)
                gdl_cmd = [
                    settings.GALLERYDL_BIN,
                    "--dest", str(temp_dir),
                    "--filename", "{category}_{id}_{num}.{extension}",
                    url,
                ]
                try:
                    gdl_proc = await asyncio.create_subprocess_exec(
                        *gdl_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(gdl_proc.wait(), timeout=60.0)
                    downloaded_files = [
                        f for f in temp_dir.rglob("*")
                        if f.is_file() and not f.name.endswith((".temp", ".part", ".ytdl", ".aria2"))
                    ]
                except Exception as ge:
                    logger.warning("gallery-dl fallback execution failed: %s", ge)

            if not downloaded_files:
                return False, None, "No output file found after download completed.", temp_dir

            # Sort files
            image_extensions = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
            audio_extensions = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav")
            video_extensions = (".mp4", ".mkv", ".webm", ".mov", ".avi")

            video_files = [f for f in downloaded_files if f.suffix.lower() in video_extensions]
            audio_files = [f for f in downloaded_files if f.suffix.lower() in audio_extensions]
            image_files = [f for f in downloaded_files if f.suffix.lower() in image_extensions]
            other_files = [f for f in downloaded_files if f not in video_files and f not in audio_files and f not in image_files]

            if video_files:
                target_file = video_files[0]
                media_type = "video"
                thumb_path = image_files[0] if image_files else None
                extra_files = video_files[1:]
            elif audio_files:
                target_file = audio_files[0]
                media_type = "audio"
                thumb_path = image_files[0] if image_files else None
                extra_files = audio_files[1:]
            elif image_files:
                target_file = image_files[0]
                media_type = "photo" if len(image_files) == 1 else "gallery"
                thumb_path = None
                extra_files = image_files[1:]
            else:
                target_file = other_files[0] if other_files else downloaded_files[0]
                media_type = "document"
                thumb_path = None
                extra_files = other_files[1:]

            file_size = target_file.stat().st_size
            total_size = sum(f.stat().st_size for f in downloaded_files)

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

            # Probe video metadata using ffprobe if available
            duration = None
            width = None
            height = None
            if media_type == "video":
                video_meta = await self._probe_file_streams(target_file)
                if video_meta:
                    duration = video_meta.get("duration")
                    width = video_meta.get("width")
                    height = video_meta.get("height")

            result = DownloadResult(
                temp_dir=temp_dir,
                file_path=target_file,
                filename=target_file.name,
                file_size_bytes=total_size,
                title=target_file.stem,
                duration=duration,
                thumbnail_path=thumb_path,
                media_type=media_type,
                width=width,
                height=height,
                extra_files=extra_files,
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
            "--newline",
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
