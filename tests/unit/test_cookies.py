from pathlib import Path

from app.utils.cookies import get_cookie_file


def test_cookie_file_exists(tmp_path: Path) -> None:
    f = tmp_path / "instagram.txt"
    f.write_text("cookie", encoding="utf-8")
    result = get_cookie_file(tmp_path, "instagram")
    assert result.exists
    assert result.path == f


def test_cookie_file_missing(tmp_path: Path) -> None:
    result = get_cookie_file(tmp_path, "tiktok")
    assert not result.exists
    assert "missing" in (result.reason or "")


def test_cookie_file_alias_for_instagram(tmp_path: Path) -> None:
    f = tmp_path / "ig.txt"
    f.write_text("cookie", encoding="utf-8")
    result = get_cookie_file(tmp_path, "instagram")
    assert result.exists
    assert result.path == f
