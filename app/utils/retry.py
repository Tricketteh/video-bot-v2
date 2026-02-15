from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def retry_async[T](
    fn: Callable[[], Awaitable[T]],
    attempts: int,
    base_delay: float,
    retriable: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except retriable as exc:
            last_error = exc
            if attempt == attempts:
                break
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error
