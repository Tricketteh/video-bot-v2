from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MediaType = Literal["video", "photo"]
DownloadMethod = Literal["yt-dlp", "gallery-dl", "direct-http"]


@dataclass(slots=True)
class MediaItem:
    type: MediaType
    source_url: str
    platform: str
    meta: dict[str, Any] = field(default_factory=dict)
    index: int = 0
    estimated_size: int | None = None
    download_method: DownloadMethod = "yt-dlp"


@dataclass(slots=True)
class DownloadResult:
    files: list[str]
    method: str