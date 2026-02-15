from __future__ import annotations

from urllib.parse import urlparse


def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "instagram" in host:
        return "instagram"
    if "tiktok" in host:
        return "tiktok"
    if "youtu.be" in host or "youtube" in host:
        return "youtube"
    if "redgifs" in host:
        return "redgifs"
    return "unknown"


def is_instagram_post_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    return "/p/" in path


def is_instagram_reel_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    return "/reel/" in path or "/reels/" in path


def is_tiktok_photo_path(url: str) -> bool:
    return "/photo/" in urlparse(url).path.lower()


def is_youtube_shorts(url: str) -> bool:
    return "/shorts/" in urlparse(url).path.lower()