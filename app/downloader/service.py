from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import sys
import time
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import requests
import yt_dlp

from app.downloader.models import DownloadResult, MediaItem
from app.downloader.platforms import (
    detect_platform,
    is_instagram_post_path,
    is_tiktok_photo_path,
    is_youtube_shorts,
)
from app.utils.config import Settings
from app.utils.cookies import get_cookie_file
from app.utils.logging_config import log_event
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    pass


class DuplicateRecentlyProcessed(DownloadError):
    pass


class UnsupportedUrl(DownloadError):
    pass


class DownloaderService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._recent: deque[str] = deque(maxlen=settings.recent_cache_size)
        self._recent_set: set[str] = set()
        self._inflight: dict[str, asyncio.Task[list[str]]] = {}
        self._lock = asyncio.Lock()
        self._last_successful_download_ts: float | None = None
        self._platform_semaphores: dict[str, asyncio.Semaphore] = {
            "instagram": asyncio.Semaphore(settings.instagram_concurrency),
            "tiktok": asyncio.Semaphore(settings.tiktok_concurrency),
            "youtube": asyncio.Semaphore(settings.youtube_concurrency),
            "redgifs": asyncio.Semaphore(settings.redgifs_concurrency),
            "unknown": asyncio.Semaphore(settings.unknown_concurrency),
        }

    @property
    def last_successful_download_ts(self) -> float | None:
        return self._last_successful_download_ts

    async def normalize_url(self, url: str) -> str:
        def _resolve() -> str:
            try:
                response = requests.get(
                    url,
                    allow_redirects=True,
                    timeout=10,
                    headers={"User-Agent": self.settings.http_user_agent},
                )
                return response.url
            except requests.RequestException:
                return url

        return await asyncio.to_thread(_resolve)

    async def extract_media_items(self, url: str) -> list[MediaItem]:
        normalized = await self.normalize_url(url)
        platform = detect_platform(normalized)
        if platform == "unknown":
            raise UnsupportedUrl(f"Unsupported URL: {normalized}")

        if platform == "youtube" and not is_youtube_shorts(normalized):
            raise UnsupportedUrl("Only YouTube Shorts are supported")

        if platform == "instagram" and is_instagram_post_path(normalized):
            gallery_items = await self._try_gallery_extract(normalized, platform)
            if gallery_items:
                return gallery_items
            raise DownloadError(
                "Instagram post (/p/) did not return photo items via gallery-dl. "
                "Check cookies/instagram.txt (or cookies/ig.txt)."
            )

        if platform == "tiktok" and is_tiktok_photo_path(normalized):
            gallery_items = await self._try_gallery_extract(normalized, platform)
            if gallery_items:
                return gallery_items
            raise DownloadError(
                "TikTok photo URL was detected, but photo extractor returned no items"
            )

        if platform in {"instagram", "tiktok", "redgifs", "youtube"}:
            return await self._yt_extract(normalized, platform)

        raise UnsupportedUrl(f"No strategy for URL: {normalized}")

    async def download_media_item(self, media_item: MediaItem, tmpdir: Path) -> str:
        if media_item.type == "video":
            if media_item.download_method == "direct-http":
                return await self._download_binary_direct(
                    media_item,
                    tmpdir,
                    default_ext=".mp4",
                )
            return await self._download_video_with_yt(media_item, tmpdir)

        return await self._download_binary_direct(media_item, tmpdir, default_ext=".jpg")

    async def download_media_items(self, url: str) -> DownloadResult:
        normalized = await self.normalize_url(url)
        url_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

        async with self._lock:
            inflight = self._inflight.get(url_hash)
            if inflight:
                files = await inflight
                return DownloadResult(files=files, method="dedup-wait")

            if url_hash in self._recent_set:
                raise DuplicateRecentlyProcessed("URL already processed recently")

            task = asyncio.create_task(self._download_pipeline(normalized, url_hash))
            self._inflight[url_hash] = task

        try:
            files = await task
            return DownloadResult(files=files, method="pipeline")
        finally:
            async with self._lock:
                self._inflight.pop(url_hash, None)

    async def _download_pipeline(self, normalized_url: str, url_hash: str) -> list[str]:
        platform = detect_platform(normalized_url)
        semaphore = self._platform_semaphores.get(platform, self._platform_semaphores["unknown"])

        async with semaphore:
            log_event(
                logger,
                logging.INFO,
                "PIPELINE_START",
                "download pipeline started",
                url=normalized_url,
                platform=platform,
                url_hash=url_hash[:12],
            )
            if platform == "instagram" and is_instagram_post_path(normalized_url):
                direct_files = await self._download_gallery_page(normalized_url, platform, url_hash)
                if direct_files:
                    self._mark_recent(url_hash)
                    self._last_successful_download_ts = asyncio.get_running_loop().time()
                    log_event(
                        logger,
                        logging.INFO,
                        "PIPELINE_DONE",
                        "download pipeline completed via gallery page",
                        url=normalized_url,
                        platform=platform,
                        url_hash=url_hash[:12],
                        file_count=len(direct_files),
                    )
                    return direct_files

            if platform == "tiktok" and is_tiktok_photo_path(normalized_url):
                direct_files = await self._download_gallery_page(normalized_url, platform, url_hash)
                if direct_files:
                    self._mark_recent(url_hash)
                    self._last_successful_download_ts = asyncio.get_running_loop().time()
                    log_event(
                        logger,
                        logging.INFO,
                        "PIPELINE_DONE",
                        "download pipeline completed via gallery page",
                        url=normalized_url,
                        platform=platform,
                        url_hash=url_hash[:12],
                        file_count=len(direct_files),
                    )
                    return direct_files

            items = await self.extract_media_items(normalized_url)
            tmp_root = self.settings.download_tmp_root
            tmp_root.mkdir(parents=True, exist_ok=True)
            per_url_dir = tmp_root / url_hash[:12]
            per_url_dir.mkdir(parents=True, exist_ok=True)

            files: list[str] = []
            for item in items:
                async def _download_current(current: MediaItem = item) -> str:
                    return await self.download_media_item(current, per_url_dir)

                path = await retry_async(
                    _download_current,
                    attempts=self.settings.retry_attempts,
                    base_delay=self.settings.retry_base_delay,
                    retriable=(DownloadError, requests.RequestException, RuntimeError),
                )
                files.append(path)

            self._mark_recent(url_hash)
            self._last_successful_download_ts = asyncio.get_running_loop().time()
            log_event(
                logger,
                logging.INFO,
                "PIPELINE_DONE",
                "download pipeline completed",
                url=normalized_url,
                platform=platform,
                url_hash=url_hash[:12],
                file_count=len(files),
            )
            return files

    def _mark_recent(self, url_hash: str) -> None:
        if len(self._recent) == self._recent.maxlen and self._recent:
            dropped = self._recent.popleft()
            self._recent_set.discard(dropped)
        self._recent.append(url_hash)
        self._recent_set.add(url_hash)

    async def _try_gallery_extract(self, url: str, platform: str) -> list[MediaItem]:
        def _run() -> list[MediaItem]:
            cookie = get_cookie_file(self.settings.cookies_dir, platform)
            cmd = [sys.executable, "-m", "gallery_dl", "--dump-json"]
            cmd.extend(["-o", f"extractor.user-agent={self.settings.http_user_agent}"])
            if cookie.exists and cookie.path:
                cmd.extend(["--cookies", str(cookie.path)])
            cmd.append(url)
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                stderr = proc.stderr.lower()
                reason = (
                    "rate_limited"
                    if "rate" in stderr
                    else "login_required" if "login" in stderr else "gallery_error"
                )
                logger.warning(
                    "gallery extraction failed",
                    extra={
                        "extra": {
                            "platform": platform,
                            "reason": reason,
                            "stderr": proc.stderr[:200],
                        }
                    },
                )
                return []

            items: list[MediaItem] = []
            for idx, line in enumerate(proc.stdout.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                direct_url: str | None = None
                ext = ""
                filesize: int | None = None
                raw_meta: dict[str, object] = {}

                if isinstance(data, str):
                    if self._is_http_url(data):
                        direct_url = data
                elif isinstance(data, dict):
                    candidate = self._pick_media_url(data)
                    if isinstance(candidate, str) and self._is_http_url(candidate):
                        direct_url = candidate
                    ext = str(data.get("extension", "")).lower()
                    if isinstance(data.get("filesize"), int):
                        filesize = data["filesize"]
                    raw_meta = data
                elif isinstance(data, list):
                    for candidate in self._iter_http_urls(data):
                        if self._looks_like_media_url(candidate):
                            direct_url = candidate
                            break

                if not direct_url:
                    continue

                parsed_url = urlparse(str(direct_url))
                ext = ext or (
                    Path(parsed_url.path).suffix.lower().lstrip(".")
                )
                media_type = "video" if ext in {"mp4", "webm", "mov"} else "photo"
                method = "direct-http"
                items.append(
                    MediaItem(
                        type=media_type,  # type: ignore[arg-type]
                        source_url=direct_url,
                        platform=platform,
                        meta={"raw": raw_meta} if raw_meta else {},
                        index=idx,
                        estimated_size=filesize,
                        download_method=method,  # type: ignore[arg-type]
                    )
                )
            return items

        return await asyncio.to_thread(_run)

    @staticmethod
    def _is_http_url(value: str) -> bool:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    async def _download_gallery_page(self, url: str, platform: str, url_hash: str) -> list[str]:
        def _run() -> list[str]:
            tmp_root = self.settings.download_tmp_root
            tmp_root.mkdir(parents=True, exist_ok=True)
            per_url_dir = tmp_root / url_hash[:12]
            per_url_dir.mkdir(parents=True, exist_ok=True)

            cookie = get_cookie_file(self.settings.cookies_dir, platform)
            cmd = [sys.executable, "-m", "gallery_dl", "--dest", str(per_url_dir), url]
            cmd.extend(["-o", f"extractor.user-agent={self.settings.http_user_agent}"])
            if cookie.exists and cookie.path:
                cmd.extend(["--cookies", str(cookie.path)])

            before = {p.resolve() for p in per_url_dir.rglob("*") if p.is_file()}
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            after = {p.resolve() for p in per_url_dir.rglob("*") if p.is_file()}
            created = [p for p in sorted(after - before) if p.suffix.lower() in {
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                ".mp4",
                ".webm",
                ".mov",
            }]

            if created:
                return [str(p) for p in created]

            if proc.returncode != 0:
                logger.warning(
                    "gallery page download failed",
                    extra={
                        "extra": {
                            "platform": platform,
                            "url": url,
                            "stderr": proc.stderr[:300],
                        }
                    },
                )
            else:
                logger.warning(
                    "gallery page download returned no files",
                    extra={
                        "extra": {
                            "platform": platform,
                            "url": url,
                            "stdout": proc.stdout[:300],
                        }
                    },
                )
            return []

        return await asyncio.to_thread(_run)

    def _pick_media_url(self, payload: dict[str, object]) -> str | None:
        candidates = [u for u in self._iter_http_urls(payload) if self._looks_like_media_url(u)]
        if not candidates:
            return None
        # Prefer direct media links, but keep fallback to the first valid URL.
        candidates.sort(key=self._media_url_score, reverse=True)
        return candidates[0]

    def _iter_http_urls(self, payload: object) -> Iterator[str]:
        if isinstance(payload, str):
            if self._is_http_url(payload):
                yield payload
            return

        if isinstance(payload, dict):
            for value in payload.values():
                yield from self._iter_http_urls(value)
            return

        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_http_urls(item)

    @staticmethod
    def _looks_like_media_url(url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        media_ext = (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".webm", ".mov")
        if path.endswith(media_ext):
            return True
        media_hosts = ("cdninstagram", "fbcdn", "tiktokcdn", "muscdn", "akamaized")
        return any(h in host for h in media_hosts)

    @staticmethod
    def _media_url_score(url: str) -> int:
        parsed = urlparse(url)
        path = parsed.path.lower()
        if path.endswith((".mp4", ".webm", ".mov")):
            return 100
        if path.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return 90
        if "cdninstagram" in parsed.netloc.lower() or "fbcdn" in parsed.netloc.lower():
            return 80
        return 10

    async def _yt_extract(self, url: str, platform: str) -> list[MediaItem]:
        def _run() -> list[MediaItem]:
            cookie = get_cookie_file(self.settings.cookies_dir, platform)
            opts: dict[str, object] = {
                "quiet": True,
                "skip_download": True,
                "extract_flat": False,
                "noplaylist": False,
                "http_headers": {"User-Agent": self.settings.http_user_agent},
            }
            if cookie.exists and cookie.path:
                opts["cookiefile"] = str(cookie.path)

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            entries = info.get("entries") if isinstance(info, dict) else None
            raw_items = entries if isinstance(entries, list) and entries else [info]
            items: list[MediaItem] = []
            for idx, raw in enumerate(raw_items):
                if not isinstance(raw, dict):
                    continue
                source_url = raw.get("webpage_url") or raw.get("url") or url
                items.append(
                    MediaItem(
                        type="video",
                        source_url=str(source_url),
                        platform=platform,
                        meta={"id": raw.get("id"), "title": raw.get("title")},
                        index=idx,
                        estimated_size=(
                            raw.get("filesize_approx")
                            if isinstance(raw.get("filesize_approx"), int)
                            else None
                        ),
                        download_method="yt-dlp",
                    )
                )
            if not items:
                raise DownloadError("No media items extracted by yt-dlp")
            return items

        return await asyncio.to_thread(_run)

    async def _download_video_with_yt(self, media_item: MediaItem, tmpdir: Path) -> str:
        def _run() -> str:
            cookie = get_cookie_file(self.settings.cookies_dir, media_item.platform)
            outtmpl = str(tmpdir / f"{media_item.index:03d}.%(ext)s")
            progress_state = {"last_emit": 0.0}
            progress_interval = self.settings.download_progress_log_interval_seconds

            def _hook(payload: dict[str, object]) -> None:
                status = str(payload.get("status", "unknown"))
                now = time.monotonic()
                if status == "downloading" and now - progress_state["last_emit"] < progress_interval:
                    return

                downloaded = int(payload.get("downloaded_bytes", 0) or 0)
                total = int(
                    payload.get("total_bytes")
                    or payload.get("total_bytes_estimate")
                    or media_item.estimated_size
                    or 0
                )
                speed = float(payload.get("speed", 0.0) or 0.0)
                eta = payload.get("eta")
                eta_int = int(eta) if isinstance(eta, (int, float)) else None

                log_event(
                    logger,
                    logging.INFO,
                    "DOWNLOAD_PROGRESS",
                    "yt-dlp download progress",
                    method="yt-dlp",
                    platform=media_item.platform,
                    source_url=media_item.source_url,
                    item_index=media_item.index,
                    status=status,
                    downloaded_bytes=downloaded,
                    total_bytes=total if total > 0 else None,
                    percent=round((downloaded / total) * 100, 2) if total > 0 else None,
                    speed_bps=round(speed, 2) if speed else None,
                    eta_seconds=eta_int,
                )
                progress_state["last_emit"] = now

            log_event(
                logger,
                logging.INFO,
                "DOWNLOAD_START",
                "yt-dlp download started",
                method="yt-dlp",
                platform=media_item.platform,
                source_url=media_item.source_url,
                item_index=media_item.index,
            )
            opts: dict[str, object] = {
                "outtmpl": outtmpl,
                "format": "bv*+ba/b",
                "merge_output_format": "mp4",
                "quiet": True,
                "noprogress": True,
                "retries": self.settings.retry_attempts,
                "progress_hooks": [_hook],
                "http_headers": {"User-Agent": self.settings.http_user_agent},
            }
            if cookie.exists and cookie.path:
                opts["cookiefile"] = str(cookie.path)

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([media_item.source_url])

            candidates = sorted(tmpdir.glob(f"{media_item.index:03d}.*"))
            if not candidates:
                raise DownloadError("yt-dlp finished but no file created")
            final_size = candidates[0].stat().st_size
            log_event(
                logger,
                logging.INFO,
                "DOWNLOAD_DONE",
                "yt-dlp download finished",
                method="yt-dlp",
                platform=media_item.platform,
                source_url=media_item.source_url,
                item_index=media_item.index,
                target_path=str(candidates[0]),
                file_size_bytes=final_size,
            )
            return str(candidates[0])

        try:
            return await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger,
                logging.WARNING,
                "DOWNLOAD_FAIL",
                "yt-dlp download failed",
                method="yt-dlp",
                platform=media_item.platform,
                source_url=media_item.source_url,
                item_index=media_item.index,
                reason=str(exc),
            )
            msg = str(exc).lower()
            if "login" in msg:
                raise DownloadError("Login required") from exc
            if "rate" in msg or "429" in msg:
                raise DownloadError("Rate-limited") from exc
            raise DownloadError(f"Video download failed: {exc}") from exc

    async def _download_binary_direct(
        self,
        media_item: MediaItem,
        tmpdir: Path,
        default_ext: str,
    ) -> str:
        def _run() -> str:
            parsed = urlparse(media_item.source_url)
            ext = Path(parsed.path).suffix or default_ext
            target = tmpdir / f"{media_item.index:03d}{ext}"
            started = time.monotonic()
            last_emit = started
            progress_interval = self.settings.download_progress_log_interval_seconds

            log_event(
                logger,
                logging.INFO,
                "DOWNLOAD_START",
                "direct download started",
                method="direct-http",
                platform=media_item.platform,
                source_url=media_item.source_url,
                item_index=media_item.index,
                target_path=str(target),
                expected_size_bytes=media_item.estimated_size,
            )
            with requests.get(
                media_item.source_url,
                stream=True,
                timeout=30,
                headers={"User-Agent": self.settings.http_user_agent},
            ) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length", "0") or 0)
                downloaded = 0
                with target.open("wb") as fh:
                    for chunk in response.iter_content(chunk_size=1024 * 128):
                        if chunk:
                            fh.write(chunk)
                            downloaded += len(chunk)
                            now = time.monotonic()
                            if now - last_emit >= progress_interval:
                                elapsed = max(now - started, 0.001)
                                speed = downloaded / elapsed
                                eta = int((total - downloaded) / speed) if total > 0 and speed > 0 else None
                                log_event(
                                    logger,
                                    logging.INFO,
                                    "DOWNLOAD_PROGRESS",
                                    "direct download progress",
                                    method="direct-http",
                                    platform=media_item.platform,
                                    source_url=media_item.source_url,
                                    item_index=media_item.index,
                                    downloaded_bytes=downloaded,
                                    total_bytes=total if total > 0 else None,
                                    percent=round((downloaded / total) * 100, 2)
                                    if total > 0
                                    else None,
                                    speed_bps=round(speed, 2),
                                    eta_seconds=eta,
                                )
                                last_emit = now

            finished = time.monotonic()
            size = target.stat().st_size if target.exists() else 0
            log_event(
                logger,
                logging.INFO,
                "DOWNLOAD_DONE",
                "direct download finished",
                method="direct-http",
                platform=media_item.platform,
                source_url=media_item.source_url,
                item_index=media_item.index,
                target_path=str(target),
                file_size_bytes=size,
                duration_seconds=round(finished - started, 2),
            )
            return str(target)

        try:
            return await asyncio.to_thread(_run)
        except requests.RequestException as exc:
            log_event(
                logger,
                logging.WARNING,
                "DOWNLOAD_FAIL",
                "direct download failed",
                method="direct-http",
                platform=media_item.platform,
                source_url=media_item.source_url,
                item_index=media_item.index,
                reason=str(exc),
            )
            raise DownloadError(f"Direct download failed: {exc}") from exc
