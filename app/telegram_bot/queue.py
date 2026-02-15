from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(slots=True)
class QueueJob:
    url: str
    chat_id: int
    user_id: int
    username: str


class AsyncJobQueue:
    def __init__(self, workers: int) -> None:
        self.queue: asyncio.Queue[tuple[QueueJob, asyncio.Future]] = asyncio.Queue()
        self.workers = workers
        self._tasks: list[asyncio.Task] = []

    async def start(self, handler) -> None:
        for _ in range(self.workers):
            self._tasks.append(asyncio.create_task(self._worker(handler)))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def submit(self, job: QueueJob):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.queue.put((job, future))
        return await future

    async def _worker(self, handler) -> None:
        while True:
            job, future = await self.queue.get()
            try:
                result = await handler(job)
                if not future.done():
                    future.set_result(result)
            except Exception as exc:  # noqa: BLE001
                if not future.done():
                    future.set_exception(exc)
            finally:
                self.queue.task_done()