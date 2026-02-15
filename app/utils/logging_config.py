from __future__ import annotations

import json
import logging
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any, Callable


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            payload.update(record.extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


class ThrottledDuplicateFilter(logging.Filter):
    def __init__(
        self, min_interval_seconds: float, clock: Callable[[], float] | None = None
    ) -> None:
        super().__init__()
        self._min_interval_seconds = min_interval_seconds
        self._clock = clock or time.monotonic
        self._last_seen: dict[tuple[str, int, str], float] = {}
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, record.getMessage())
        now = self._clock()
        with self._lock:
            previous = self._last_seen.get(key)
            if previous is not None and now - previous < self._min_interval_seconds:
                return False
            self._last_seen[key] = now
        return True


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    formatter = (
        JsonFormatter()
        if json_logs
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.setFormatter(formatter)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )

    httpx_logger = logging.getLogger("httpx")
    httpx_logger.filters = [
        current
        for current in httpx_logger.filters
        if not isinstance(current, ThrottledDuplicateFilter)
    ]
    httpx_logger.addFilter(ThrottledDuplicateFilter(min_interval_seconds=300))
