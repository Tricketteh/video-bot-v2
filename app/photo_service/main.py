from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from app.downloader.service import DownloadError, DownloaderService
from app.utils.config import Settings
from app.utils.logging_config import configure_logging

settings = Settings()
configure_logging(settings.log_level)
service = DownloaderService(settings)
app = FastAPI(title="video-bot-photo-service", version="1.0.0")


class DownloadRequest(BaseModel):
    url: str
    platform: str | None = None


class DownloadedItem(BaseModel):
    type: str
    filename: str
    size: int


class DownloadResponse(BaseModel):
    status: str
    items: list[DownloadedItem]


async def verify_internal_token(x_internal_token: str = Header(default="")) -> None:
    if x_internal_token != settings.internal_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


@app.get("/health")
async def health() -> dict[str, str | None]:
    ts = service.last_successful_download_ts
    iso = datetime.fromtimestamp(ts, tz=UTC).isoformat() if ts else None
    return {"status": "ok", "last_successful_download_timestamp": iso}


@app.post(
    "/v1/download",
    response_model=DownloadResponse,
    dependencies=[Depends(verify_internal_token)],
)
async def download(payload: DownloadRequest) -> DownloadResponse:
    try:
        result = await service.download_media_items(payload.url)
    except DownloadError as exc:
        code = (
            status.HTTP_429_TOO_MANY_REQUESTS
            if "rate" in str(exc).lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    items: list[DownloadedItem] = []
    for file in result.files:
        p = Path(file)
        items.append(DownloadedItem(type=_guess_type(p), filename=p.name, size=p.stat().st_size))
    return DownloadResponse(status="ok", items=items)


def _guess_type(path: Path) -> str:
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        return "photo"
    return "video"
