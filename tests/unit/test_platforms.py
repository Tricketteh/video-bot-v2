from app.downloader.platforms import (
    detect_platform,
    is_instagram_post_path,
    is_tiktok_photo_path,
    is_youtube_shorts,
)


def test_detect_platform() -> None:
    assert detect_platform("https://www.instagram.com/p/abc/") == "instagram"
    assert detect_platform("https://www.tiktok.com/@u/video/1") == "tiktok"
    assert detect_platform("https://youtube.com/shorts/1") == "youtube"
    assert detect_platform("https://www.redgifs.com/watch/abc") == "redgifs"


def test_path_helpers() -> None:
    assert is_instagram_post_path("https://instagram.com/p/abc")
    assert is_tiktok_photo_path("https://www.tiktok.com/@u/photo/123")
    assert is_youtube_shorts("https://www.youtube.com/shorts/abc")