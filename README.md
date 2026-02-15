# video-bot-v2

Модульный Python-сервис для скачивания медиа из Instagram/TikTok/YouTube Shorts/Redgifs через Telegram-бота.

## Архитектура

- `app/telegram_bot/`: Telegram API слой (handlers, очередь, rate limit, отправка медиа).
- `app/downloader/`: независимый downloader (platform detect, extractor routing, cookies, retries, dedupe).
- `app/photo_service/`: FastAPI сервис для выделенного photo-flow (`POST /v1/download`).
- `app/utils/`: конфиг, логирование, cookies helper, retry.

Ключевое решение: Telegram-слой не содержит downloader-деталей; downloader не зависит от Telegram. Контракт передачи данных — `MediaItem` (`app/downloader/models.py`).

## Зафиксированные версии

Проверено `2026-02-14` через `python -m pip index versions <package>`:

- Python: `3.14.2`
- python-telegram-bot: `22.6`
- yt-dlp: `2026.2.4`
- gallery-dl: `1.31.6`
- fastapi: `0.129.0`
- uvicorn: `0.40.0`
- requests: `2.32.5`

Pinned в `requirements.txt`.

## Запуск локально

1. Создать `.env` на основе `.env.example`.
2. Положить cookies в `cookies/<platform>.txt` (`instagram.txt`, `tiktok.txt`, `redgifs.txt`, при необходимости `youtube.txt`).
3. Запустить:

```bash
make run-dev
```

Или:

```bash
docker compose up -d --build
```

## Команды

```bash
make build
make run-dev
make lint
make typecheck
make test
```

Проверка версий runtime:

```bash
python -c "import yt_dlp; print(yt_dlp.__version__)"
python -c "import gallery_dl; print(gallery_dl.__version__)"
python -c "import telegram; print(telegram.__version__)"
```

## API photo-service

- `GET /health` -> `{"status":"ok", "last_successful_download_timestamp": ...}`
- `POST /v1/download`
  - Header: `X-INTERNAL-TOKEN: <INTERNAL_TOKEN>`
  - Body: `{"url": "...", "platform": null|"auto"}`

## Безопасность и правила

- Не хранить токены/cookies в git.
- В логах только метаданные.
- Добавить whitelist групп (`GROUP_WHITELIST`) и per-user rate limit (`PER_USER_RATE_LIMIT`).
- Пользователь должен иметь право скачивать контент.
- Heartbeat лога listener настраивается через LISTENER_HEARTBEAT_SECONDS (по умолчанию 300).
- Интервал логов прогресса скачивания настраивается через DOWNLOAD_PROGRESS_LOG_INTERVAL_SECONDS (по умолчанию 1.0).

## CI

GitHub Actions: lint (`ruff`), typecheck (`mypy`), tests (`pytest`), docker build.

## Как обновить зависимости

```bash
python -m pip index versions yt-dlp
python -m pip index versions gallery-dl
python -m pip index versions python-telegram-bot
# обновить requirements.txt
make test
```