from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

COOKIE_ALIASES: dict[str, list[str]] = {
    "instagram": ["instagram.txt", "ig.txt"],
    "tiktok": ["tiktok.txt", "tt.txt"],
    "redgifs": ["redgifs.txt", "redgifs.txt"],
    "youtube": ["youtube.txt", "yt.txt"],
}


@dataclass(slots=True)
class CookieFileResult:
    platform: str
    path: Path | None
    exists: bool
    reason: str | None = None


def get_cookie_file(cookies_dir: Path, platform: str) -> CookieFileResult:
    candidates = COOKIE_ALIASES.get(platform, [f"{platform}.txt"])
    for candidate in candidates:
        file_path = cookies_dir / candidate
        if file_path.exists() and file_path.is_file():
            return CookieFileResult(platform=platform, path=file_path, exists=True)

    file_path = cookies_dir / candidates[0]
    return CookieFileResult(
        platform=platform,
        path=file_path,
        exists=False,
        reason=f"cookiefile missing: tried {', '.join(str(cookies_dir / c) for c in candidates)}",
    )
