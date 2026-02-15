import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app.downloader.models import MediaItem
from app.downloader.service import DownloadError, DownloaderService
from app.utils.config import Settings


@pytest.mark.asyncio
async def test_dedupe_inflight(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(download_tmp_root=tmp_path)
    service = DownloaderService(settings)

    calls = 0

    async def fake_pipeline(url: str, url_hash: str) -> list[str]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return [str(tmp_path / f"{url_hash}.mp4")]

    monkeypatch.setattr(service, "_download_pipeline", fake_pipeline)
    monkeypatch.setattr(service, "normalize_url", lambda url: asyncio.sleep(0, result=url))

    a, b = await asyncio.gather(
        service.download_media_items("https://example.com/video"),
        service.download_media_items("https://example.com/video"),
    )

    assert len(a.files) == 1
    assert len(b.files) == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_instagram_post_prefers_gallery_for_photos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = Settings(download_tmp_root=tmp_path)
    service = DownloaderService(settings)

    gallery_items = [
        MediaItem(
            type="photo",
            source_url="https://cdn.test/1.jpg",
            platform="instagram",
            index=0,
            download_method="direct-http",
        ),
        MediaItem(
            type="photo",
            source_url="https://cdn.test/2.jpg",
            platform="instagram",
            index=1,
            download_method="direct-http",
        ),
    ]

    async def fake_gallery(url: str, platform: str) -> list[MediaItem]:
        return gallery_items

    async def fake_yt(url: str, platform: str) -> list[MediaItem]:
        raise AssertionError("yt extractor should not be used")

    monkeypatch.setattr(service, "normalize_url", lambda url: asyncio.sleep(0, result=url))
    monkeypatch.setattr(service, "_try_gallery_extract", fake_gallery)
    monkeypatch.setattr(service, "_yt_extract", fake_yt)

    items = await service.extract_media_items("https://www.instagram.com/p/abc/")
    assert [item.type for item in items] == ["photo", "photo"]


@pytest.mark.asyncio
async def test_gallery_extract_accepts_json_string_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = Settings(download_tmp_root=tmp_path)
    service = DownloaderService(settings)

    class DummyProc:
        returncode = 0
        stdout = json.dumps("https://cdn.test/file.jpg")
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: DummyProc())
    items = await service._try_gallery_extract("https://www.instagram.com/p/abc/", "instagram")
    assert len(items) == 1
    assert items[0].source_url == "https://cdn.test/file.jpg"
    assert items[0].type == "photo"


@pytest.mark.asyncio
async def test_gallery_extract_skips_hashtag_strings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = Settings(download_tmp_root=tmp_path)
    service = DownloaderService(settings)

    class DummyProc:
        returncode = 0
        stdout = json.dumps("#힙코치")
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: DummyProc())
    items = await service._try_gallery_extract("https://www.instagram.com/p/abc/", "instagram")
    assert items == []


@pytest.mark.asyncio
async def test_gallery_extract_reads_nested_media_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = Settings(download_tmp_root=tmp_path)
    service = DownloaderService(settings)

    nested = {
        "id": "123",
        "edge_sidecar_to_children": {
            "edges": [
                {
                    "node": {
                        "display_url": "https://scontent.cdninstagram.com/path/photo1.jpg",
                    }
                }
            ]
        },
    }

    class DummyProc:
        returncode = 0
        stdout = json.dumps(nested)
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: DummyProc())
    items = await service._try_gallery_extract("https://www.instagram.com/p/abc/", "instagram")
    assert len(items) == 1
    assert items[0].source_url.endswith("photo1.jpg")


@pytest.mark.asyncio
async def test_instagram_post_pipeline_does_not_fallback_to_second_extract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = Settings(download_tmp_root=tmp_path)
    service = DownloaderService(settings)

    async def fake_gallery_page(url: str, platform: str, url_hash: str) -> list[str]:
        return []

    async def fail_extract(_: str) -> list[MediaItem]:
        raise AssertionError("extract_media_items should not be called for instagram /p/ fallback")

    monkeypatch.setattr(service, "_download_gallery_page", fake_gallery_page)
    monkeypatch.setattr(service, "extract_media_items", fail_extract)

    with pytest.raises(DownloadError):
        await service._download_pipeline(
            "https://www.instagram.com/p/abc/",
            "a" * 64,
        )
