import logging

from app.utils.logging_config import ThrottledDuplicateFilter


def _make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_throttled_duplicate_filter_blocks_repeat_within_window() -> None:
    ticks = iter([0.0, 10.0, 301.0])
    f = ThrottledDuplicateFilter(300, clock=lambda: next(ticks))

    first = _make_record("HTTP Request: POST .../getUpdates")
    second = _make_record("HTTP Request: POST .../getUpdates")
    third = _make_record("HTTP Request: POST .../getUpdates")

    assert f.filter(first) is True
    assert f.filter(second) is False
    assert f.filter(third) is True
