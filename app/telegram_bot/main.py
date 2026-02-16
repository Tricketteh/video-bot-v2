from __future__ import annotations

import asyncio
import html
import logging
import re
import shutil
import time
from collections import defaultdict, deque
from contextlib import ExitStack
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from telegram import (
    InlineQueryResultArticle,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from app.downloader.service import DownloadError, DownloaderService
from app.telegram_bot.queue import AsyncJobQueue, QueueJob
from app.utils.config import Settings
from app.utils.logging_config import configure_logging, log_event

URL_RE = re.compile(r"https?://\S+")
logger = logging.getLogger(__name__)

settings = Settings()
configure_logging(settings.log_level)
downloader = DownloaderService(settings)
job_queue = AsyncJobQueue(settings.queue_workers)
health_app = FastAPI(title="video-bot-health")
user_rate_history: dict[int, deque[float]] = defaultdict(deque)


@health_app.get("/health")
async def health() -> dict[str, str | None]:
    ts = downloader.last_successful_download_ts
    return {"status": "ok", "last_successful_download_timestamp": str(ts) if ts else None}


async def _download_job(job: QueueJob) -> list[str]:
    log_event(
        logger,
        logging.INFO,
        "DOWNLOAD_JOB_START",
        "processing queued download job",
        url=job.url,
        chat_id=job.chat_id,
        user_id=job.user_id,
    )
    result = await downloader.download_media_items(job.url)
    log_event(
        logger,
        logging.INFO,
        "DOWNLOAD_JOB_DONE",
        "download job completed",
        url=job.url,
        chat_id=job.chat_id,
        user_id=job.user_id,
        file_count=len(result.files),
    )
    return result.files


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "Send URL(s). Supported: Instagram, TikTok, YouTube Shorts, Redgifs."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return

    chat = update.effective_chat
    if not chat:
        return

    if not _is_allowed_chat(chat.id, chat.type):
        return

    user = update.effective_user
    if not user:
        return

    urls = URL_RE.findall(message.text)
    if not urls:
        return

    urls = urls[: settings.max_links_per_message]
    if not _allowed_by_rate_limit(user.id):
        log_event(
            logger,
            logging.INFO,
            "MESSAGE_RATE_LIMITED",
            "user exceeded per-window message rate limit",
            chat_id=chat.id,
            user_id=user.id,
            url_count=len(urls),
        )
        return

    log_event(
        logger,
        logging.INFO,
        "MESSAGE_URLS_ACCEPTED",
        "accepted message with media urls",
        chat_id=chat.id,
        user_id=user.id,
        url_count=len(urls),
    )

    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete source message")

    for url in urls:
        progress_msg = await context.bot.send_message(chat_id=chat.id, text="Downloading...")
        job = QueueJob(
            url=url,
            chat_id=chat.id,
            user_id=user.id,
            username=user.username or user.full_name,
        )
        try:
            files = await job_queue.submit(job)
            await _send_files(context, chat.id, files, job.username, url)
            await progress_msg.delete()
            log_event(
                logger,
                logging.INFO,
                "MESSAGE_URL_DONE",
                "url processed and sent to chat",
                chat_id=chat.id,
                user_id=user.id,
                url=url,
                file_count=len(files),
            )
        except DownloadError as exc:
            log_event(
                logger,
                logging.WARNING,
                "MESSAGE_URL_FAIL",
                "download failed",
                url=url,
                chat_id=chat.id,
                user_id=user.id,
                reason=str(exc),
            )
            await progress_msg.edit_text(f"Error: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected handling failure")
            await progress_msg.edit_text(f"Error: {exc}")


async def handle_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    query = update.inline_query
    if not query:
        return
    text = query.query.strip()
    if URL_RE.search(text):
        title = "Use PM/Group download flow"
        body = "Inline accepted. Send the URL in a direct message or group to download media."
    else:
        title = "Paste a URL"
        body = "Supported: Instagram, TikTok, YouTube Shorts, Redgifs."
    result = InlineQueryResultArticle(
        id="video-bot-inline-info",
        title=title,
        input_message_content=InputTextMessageContent(body),
        description=body,
    )
    await query.answer([result], cache_time=1, is_personal=True)


async def _send_files(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    files: list[str],
    username: str,
    url: str,
) -> None:
    author = _format_sender(username)
    safe_url = html.escape(url, quote=True)
    safe_author = html.escape(author)
    caption = f'<a href="{safe_url}">Media</a> sent by {safe_author}'
    if len(files) == 1:
        await _send_single(context, chat_id, Path(files[0]), caption)
        _cleanup_files(files)
        return

    for i in range(0, len(files), 10):
        chunk = files[i : i + 10]
        media: list[InputMediaPhoto | InputMediaVideo | InputMediaDocument] = []
        with ExitStack() as stack:
            for idx, file in enumerate(chunk):
                path = Path(file)
                current_caption = caption if i == 0 and idx == 0 else None
                file_handle = stack.enter_context(path.open("rb"))
                if _is_photo(path):
                    media.append(
                        InputMediaPhoto(
                            media=file_handle,
                            caption=current_caption,
                            parse_mode=ParseMode.HTML,
                        )
                    )
                elif _is_video(path) and path.stat().st_size <= settings.max_bot_file_bytes:
                    media.append(
                        InputMediaVideo(
                            media=file_handle,
                            caption=current_caption,
                            parse_mode=ParseMode.HTML,
                        )
                    )
                else:
                    media.append(
                        InputMediaDocument(
                            media=file_handle,
                            caption=current_caption,
                            parse_mode=ParseMode.HTML,
                        )
                    )
            await context.bot.send_media_group(chat_id=chat_id, media=media)
    _cleanup_files(files)


async def _send_single(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, path: Path, caption: str
) -> None:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if _is_photo(path):
            await context.bot.send_photo(
                chat_id=chat_id, photo=fh, caption=caption, parse_mode=ParseMode.HTML
            )
            return
        if _is_video(path) and size <= settings.max_bot_file_bytes:
            await context.bot.send_video(
                chat_id=chat_id, video=fh, caption=caption, parse_mode=ParseMode.HTML
            )
            return
        await context.bot.send_document(
            chat_id=chat_id, document=fh, caption=caption, parse_mode=ParseMode.HTML
        )


def _format_sender(username: str) -> str:
    handle = username.strip()
    if not handle:
        return "@unknown"
    if " " in handle:
        return handle
    if handle.startswith("@"):
        return handle
    return f"@{handle}"


def _is_photo(path: Path) -> bool:
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}


def _cleanup_files(files: list[str]) -> None:
    parents = {Path(f).parent for f in files}
    for p in parents:
        shutil.rmtree(p, ignore_errors=True)


def _allowed_by_rate_limit(user_id: int) -> bool:
    now = time.time()
    history = user_rate_history[user_id]
    while history and now - history[0] > settings.rate_window_seconds:
        history.popleft()
    if len(history) >= settings.per_user_rate_limit:
        return False
    history.append(now)
    return True


def _is_allowed_chat(chat_id: int, chat_type: str) -> bool:
    if chat_type == ChatType.PRIVATE:
        return True
    allowed = settings.allowed_group_ids
    if not allowed:
        return True
    return chat_id in allowed


async def _listener_heartbeat(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        log_event(
            logger,
            logging.INFO,
            "LISTENER_ACTIVE",
            "bot listener is active",
            interval_seconds=settings.listener_heartbeat_seconds,
        )
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.listener_heartbeat_seconds
            )
        except TimeoutError:
            continue


async def run() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    app = Application.builder().token(settings.bot_token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(InlineQueryHandler(handle_inline))

    health_config = uvicorn.Config(
        health_app,
        host="0.0.0.0",
        port=settings.health_port,
        log_level="warning",
    )
    health_server = uvicorn.Server(health_config)
    health_task = asyncio.create_task(health_server.serve())
    heartbeat_stop = asyncio.Event()
    heartbeat_task: asyncio.Task | None = None

    await job_queue.start(_download_job)
    try:
        log_event(logger, logging.INFO, "BOT_STARTING", "initializing telegram bot")
        await app.initialize()
        await app.start()
        if app.updater is None:
            raise RuntimeError("Updater is not available")
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        heartbeat_task = asyncio.create_task(_listener_heartbeat(heartbeat_stop))
        log_event(logger, logging.INFO, "BOT_POLLING", "telegram polling started")
        while True:
            await asyncio.sleep(3600)
    finally:
        heartbeat_stop.set()
        if heartbeat_task is not None:
            await heartbeat_task
        health_server.should_exit = True
        await health_task
        if app.updater is not None:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await job_queue.stop()


if __name__ == "__main__":
    asyncio.run(run())
