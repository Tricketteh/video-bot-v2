from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    internal_token: str = os.getenv("INTERNAL_TOKEN", "change-me")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    max_links_per_message: int = int(os.getenv("MAX_LINKS_PER_MESSAGE", "5"))
    max_bot_file_bytes: int = int(os.getenv("MAX_BOT_FILE_BYTES", str(50 * 1024 * 1024)))
    queue_workers: int = int(os.getenv("QUEUE_WORKERS", "2"))
    retry_attempts: int = int(os.getenv("RETRY_ATTEMPTS", "3"))
    retry_base_delay: float = float(os.getenv("RETRY_BASE_DELAY", "1.0"))
    recent_cache_size: int = int(os.getenv("RECENT_CACHE_SIZE", "100"))
    instagram_concurrency: int = int(os.getenv("INSTAGRAM_CONCURRENCY", "2"))
    tiktok_concurrency: int = int(os.getenv("TIKTOK_CONCURRENCY", "2"))
    youtube_concurrency: int = int(os.getenv("YOUTUBE_CONCURRENCY", "2"))
    redgifs_concurrency: int = int(os.getenv("REDGIFS_CONCURRENCY", "2"))
    unknown_concurrency: int = int(os.getenv("UNKNOWN_CONCURRENCY", "1"))
    cookies_dir: Path = Path(os.getenv("COOKIES_DIR", "cookies"))
    download_tmp_root: Path = Path(os.getenv("DOWNLOAD_TMP_ROOT", "/tmp/video-bot"))
    group_whitelist: str = os.getenv("GROUP_WHITELIST", "")
    per_user_rate_limit: int = int(os.getenv("PER_USER_RATE_LIMIT", "10"))
    rate_window_seconds: int = int(os.getenv("RATE_WINDOW_SECONDS", "60"))
    photo_service_url: str = os.getenv("PHOTO_SERVICE_URL", "http://photo-service:8080")
    enable_photo_service: bool = os.getenv("ENABLE_PHOTO_SERVICE", "0") == "1"
    health_port: int = int(os.getenv("BOT_HEALTH_PORT", "8090"))
    listener_heartbeat_seconds: int = int(os.getenv("LISTENER_HEARTBEAT_SECONDS", "300"))
    download_progress_log_interval_seconds: float = float(
        os.getenv("DOWNLOAD_PROGRESS_LOG_INTERVAL_SECONDS", "1.0")
    )
    http_user_agent: str = os.getenv(
        "HTTP_USER_AGENT",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    instagram_request_cooldown_seconds: float = float(
        os.getenv("INSTAGRAM_REQUEST_COOLDOWN_SECONDS", "2.0")
    )

    @property
    def allowed_group_ids(self) -> set[int]:
        if not self.group_whitelist.strip():
            return set()
        return {int(x.strip()) for x in self.group_whitelist.split(",") if x.strip()}
