import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, Iterable, NoReturn, Tuple, TypeVar

from expression import curry_flipped
from expression.collections import seq
from expression.core import (
    MailboxProcessor,
    TailCall,
    TailCallResult,
    aiotools,
    fst,
    pipe,
    tailrec_async,
)
from expression.system import CancellationTokenSource

from .combine import with_latest_from
from .create import interval
from .notification import Notification, OnCompleted, OnError, OnNext
from .observables import AsyncAnonymousObservable
from .observers import AsyncNotificationObserver, auto_detach_observer
from .transform import map
from .types import AsyncDisposable, AsyncObservable, AsyncObserver

_TSource = TypeVar("_TSource")
log = logging.getLogger(__name__)


@curry_flipped(1)
def delay(
    source: AsyncObservable[_TSource],
    seconds: float,
) -> AsyncObservable[_TSource]:
    """Delay observable.

    Time shifts the observable sequence by the given timeout. The
    relative time intervals between the values are preserved.

    Args:
        seconds (float): Number of seconds to delay.

    Returns:
        Delayed stream.
    """

    cts = CancellationTokenSource()
    token = cts.token

    async def subscribe_async(aobv: AsyncObserver[_TSource]) -> AsyncDisposable:
        async def worker(
            inbox: MailboxProcessor[Tuple[Notification[_TSource], datetime]]
        ) -> None:
            @tailrec_async
            async def loop() -> "TailCallResult[None, ...]":
                if token.is_cancellation_requested:
                    return

                ns, due_time = await inbox.receive()

                diff = due_time - datetime.utcnow()
                seconds = diff.total_seconds()
                if seconds > 0:
                    await asyncio.sleep(seconds)

                async def matcher() -> None:
                    with match(ns) as case:
                        for x in case(OnNext[_TSource]):
                            await aobv.asend(x)
                            return
                        for err in case(OnError[_TSource]):
                            await aobv.athrow(err)
                            return
                        for x in case(OnCompleted):
                            await aobv.aclose()
                            return

                await matcher()
                return TailCall["..."]()

            asyncio.ensure_future(loop())

        agent = MailboxProcessor.start(worker, token)

        async def fn(ns: Notification[_TSource]) -> None:
            due_time = datetime.utcnow() + timedelta(seconds=seconds)
            agent.post((ns, due_time))

        obv: AsyncNotificationObserver[_TSource] = AsyncNotificationObserver(fn)
        subscription = await source.subscribe_async(obv)

        async def cancel() -> None:
            log.debug("delay:cancel()")
            cts.cancel()
            await subscription.dispose_async()

        return AsyncDisposable.create(cancel)

    return AsyncAnonymousObservable(subscribe_async)


def debounce(
    seconds: float,
) -> Callable[[AsyncObservable[_TSource]], AsyncObservable[_TSource]]:
    def _debounce(source: AsyncObservable[_TSource]) -> AsyncObservable[_TSource]:
        async def subscribe_async(aobv: AsyncObserver[_TSource]) -> AsyncDisposable:
            safe_obv, auto_detach = auto_detach_observer(aobv)
            infinite: Iterable[int] = seq.infinite

            async def worker(
                inbox: MailboxProcessor[Tuple[Notification[_TSource], int]]
            ) -> None:
                @tailrec_async
                async def message_loop(
                    current_index: int,
                ) -> "TailCallResult[NoReturn, [int]]":
                    n, index = await inbox.receive()

                    with match(n) as case:
                        log.debug("debounce: %s, %d, %d", n, index, current_index)
                        for x in case(OnNext[_TSource]):
                            if index == current_index:
                                await safe_obv.asend(x)
                                current_index = index
                            elif index > current_index:
                                current_index = index

                        for err in case(OnError[_TSource]):
                            await safe_obv.athrow(err)

                        while case(OnCompleted):
                            await safe_obv.aclose()

                    return TailCall[int](current_index)

                await message_loop(-1)

            agent = MailboxProcessor.start(worker)

            indexer = iter(infinite)

            async def obv(n: Notification[_TSource]) -> None:
                index = next(indexer)
                agent.post((n, index))

                async def worker() -> None:
                    log.debug("debounce, sleeping: %s", seconds)
                    await asyncio.sleep(seconds)
                    agent.post((n, index))

                aiotools.start(worker())

            obv_: AsyncObserver[_TSource] = AsyncNotificationObserver(obv)
            dispose = await pipe(obv_, source.subscribe_async, auto_detach)

            async def cancel() -> None:
                await dispose.dispose_async()
                agent.post((OnCompleted, 0))

            return AsyncDisposable.create(cancel)

        return AsyncAnonymousObservable(subscribe_async)

    return _debounce


def sample(
    seconds: float,
) -> Callable[[AsyncObservable[_TSource]], AsyncObservable[_TSource]]:
    def _sample(source: AsyncObservable[_TSource]) -> AsyncObservable[_TSource]:
        timer = interval(seconds, seconds)

        if seconds > 0:
            ret = pipe(
                source,
                with_latest_from(timer),
                map(fst),
            )
            return ret

        return source

    return _sample
