from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory


class ManagedTempDir:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._td = TemporaryDirectory(prefix="dl-", dir=root)
        self.path = Path(self._td.name)

    def cleanup(self) -> None:
        self._td.cleanup()

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()