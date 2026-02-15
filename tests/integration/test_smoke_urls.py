import pytest


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/test/",
        "https://www.instagram.com/reel/test/",
        "https://www.youtube.com/shorts/test",
        "https://www.tiktok.com/@a/video/1",
        "https://www.tiktok.com/@a/photo/1",
        "https://www.redgifs.com/watch/test",
    ],
)
def test_smoke_url_samples(url: str) -> None:
    assert url.startswith("https://")