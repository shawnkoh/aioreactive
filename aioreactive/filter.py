from typing import Any, Awaitable, Callable, List, Optional, Tuple, TypeVar

from expression.collections import seq
from expression.core import Option, Result, aio, compose, fst, match, pipe
from expression.core.fn import TailCall, recursive_async
from expression.core.mailbox import MailboxProcessor
from expression.system.disposable import AsyncDisposable

from .combine import zip_seq
from .notification import Notification, OnCompleted, OnError, OnNext
from .observables import AsyncAnonymousObservable
from .observers import AsyncAnonymousObserver, AsyncNotificationObserver, auto_detach_observer
from .transform import map, transform
from .types import AsyncObservable, AsyncObserver, Stream

TSource = TypeVar("TSource")
TResult = TypeVar("TResult")


def choose_async(chooser: Callable[[TSource], Awaitable[Option[TResult]]]) -> Stream[TSource, TResult]:
    """Choose async.

    Applies the given async function to each element of the stream and
    returns the stream comprised of the results for each element where
    the function returns Some with some value.

    Args:
        chooser (Callable[[TSource], Awaitable[Option[TResult]]]): [description]

    Returns:
        Stream[TSource, TResult]: [description]
    """

    async def handler(next: Callable[[TResult], Awaitable[None]], xs: TSource) -> None:
        result = await chooser(xs)
        for x in result.to_list():
            await next(x)

    return transform(handler)


def choose(chooser: Callable[[TSource], Option[TResult]]) -> Stream[TSource, TResult]:
    """Choose.

    Applies the given function to each element of the stream and returns
    the stream comprised of the results for each element where the
    function returns Some with some value.

    Args:
        chooser (Callable[[TSource], Option[TResult]]): [description]

    Returns:
        Stream[TSource, TResult]: [description]
    """

    def handler(next: Callable[[TResult], Awaitable[None]], xs: TSource) -> Awaitable[None]:
        for x in chooser(xs).to_list():
            return next(x)
        return aio.empty()

    return transform(handler)


def filter_async(predicate: Callable[[TSource], Awaitable[bool]]) -> Stream[TSource, TSource]:
    """Filter async.

    Filters the elements of an observable sequence based on an async
    predicate. Returns an observable sequence that contains elements
    from the input sequence that satisfy the condition.

    Args:
        predicate (Callable[[TSource], Awaitable[bool]]): [description]

    Returns:
        Stream[TSource, TSource]: [description]
    """

    async def handler(next: Callable[[TSource], Awaitable[None]], x: TSource):
        if await predicate(x):
            return await next(x)

    return transform(handler)


def filter(predicate: Callable[[TSource], bool]) -> Stream[TSource, TSource]:
    """Filter stream.

    Filters the elements of an observable sequence based on a predicate.
    Returns an observable sequence that contains elements from the input
    sequence that satisfy the condition.


    Args:
        predicate (Callable[[TSource], bool]): [description]

    Returns:
        Stream[TSource, TSource]: [description]
    """

    def handler(next: Callable[[TSource], Awaitable[None]], x: TSource) -> Awaitable[None]:
        if predicate(x):
            return next(x)
        return aio.empty()

    return transform(handler)


def starfilter(predicate: Callable[..., bool]) -> Stream[TSource, Tuple[TSource, int]]:
    """Filter and spread the arguments to the predicate.

    Filters the elements of an observable sequence based on a predicate.
    Returns:
        An observable sequence that contains elements from the input
        sequence that satisfy the condition.
    """

    def handler(next: Callable[[Tuple[TSource, ...]], Awaitable[None]], args: Tuple[TSource, ...]) -> Awaitable[None]:
        print("handler")
        if predicate(*args):
            return next(args)
        return aio.empty()

    return transform(handler)


def filteri(predicate: Callable[[TSource, int], bool]) -> Stream[TSource, TSource]:
    """Filter with index.

    Filters the elements of an observable sequence based on a predicate
    and incorporating the element's index on each element of the source.

    Returns:
        An observable sequence that contains elements from the input
        sequence that satisfy the condition.

    Args:
        predicate: Function to test each element.

    Returns:
        The filtered stream.
    """

    return compose(
        zip_seq(seq.infinite()),
        starfilter(predicate),
        map(fst),
    )


def distinct_until_changed(source: AsyncObservable[TSource]) -> AsyncObservable[TSource]:
    """Distinct until changed.

    Return an observable sequence only containing the distinct
    contiguous elements from the source sequence.

    Args:
        source (AsyncObservable[TSource]): [description]

    Returns:
        Async observable with only contiguous distinct elements.
    """

    async def subscribe_async(aobv: AsyncObserver[TSource]) -> AsyncDisposable:
        safe_obv, auto_detach = auto_detach_observer(aobv)

        async def worker(inbox: MailboxProcessor[Notification[TSource]]) -> None:
            @recursive_async
            async def message_loop(latest: Notification[TSource]) -> Result[Notification[TSource], Exception]:
                n = await inbox.receive()

                async def get_latest() -> Notification[TSource]:
                    with match(n) as m:
                        for x in m.case(OnNext):
                            if n == latest:
                                break
                            try:
                                await safe_obv.asend(x)
                            except Exception as ex:
                                await safe_obv.athrow(ex)
                            break
                        for err in m.case(OnError):
                            await safe_obv.athrow(err)
                            break
                        while m.case(OnCompleted):
                            await safe_obv.aclose()
                            break

                    return n

                latest = await get_latest()
                return TailCall(latest)

            await message_loop(OnCompleted)  # Use as sentinel value as it will not match any OnNext value

        agent = MailboxProcessor.start(worker)

        async def notification(n: Notification[TSource]) -> None:
            agent.post(n)

        obv: AsyncObserver[TSource] = AsyncNotificationObserver(notification)
        return await pipe(obv, source.subscribe_async, auto_detach)

    return AsyncAnonymousObservable(subscribe_async)


def skip(count: int) -> Stream[TSource, TSource]:
    """[summary]

    Bypasses a specified number of elements in an observable sequence
    and then returns the remaining elements.

    Args:
        count (int): [description]

    Returns:
        Stream[TSource, TSource]: [description]
    """

    def _skip(source: AsyncObservable[TSource]) -> AsyncObservable[TSource]:
        async def subscribe_async(obvAsync: AsyncObserver[TSource]) -> AsyncDisposable:
            safe_obv, auto_detach = auto_detach_observer(obvAsync)

            remaining = count

            async def asend(value: TSource) -> None:
                nonlocal remaining
                if remaining <= 0:
                    await safe_obv.asend(value)
                else:
                    remaining -= 1

            obv = AsyncAnonymousObserver(asend, safe_obv.athrow, safe_obv.aclose)
            return await pipe(obv, source.subscribe_async, auto_detach)

        return AsyncAnonymousObservable(subscribe_async)

    return _skip


def skip_last(count: int) -> Stream[TSource, TSource]:
    def _skip_last(source: AsyncObservable[TSource]) -> AsyncObservable[TSource]:
        """Bypasses a specified number of elements at the end of an
        observable sequence.

        This operator accumulates a queue with a length enough to store
        the first `count` elements. As more elements are received,
        elements are taken from the front of the queue and produced on
        the result sequence. This causes elements to be delayed.

        Args:
            count: Number of elements to bypass at the end of the
            source sequence.

        Returns:
            An observable sequence containing the source sequence
            elements except for the bypassed ones at the end.
        """

        async def subscribe_async(observer: AsyncObserver[TSource]) -> AsyncDisposable:
            safe_obv, auto_detach = auto_detach_observer(observer)

            q = []

            async def asend(value: TSource) -> None:
                front = None
                q.append(value)
                if len(q) > count:
                    front = q.pop(0)

                if front is not None:
                    await safe_obv.asend(front)

            obv = AsyncAnonymousObserver(asend, safe_obv.athrow, safe_obv.aclose)
            return await pipe(obv, source.subscribe_async, auto_detach)

        return AsyncAnonymousObservable(subscribe_async)

    return _skip_last


def take(count: int) -> Stream[TSource, TSource]:
    """Take the first elements from the stream.

    Returns a specified number of contiguous elements from the start of
    an observable sequence.

    Args:
        count Number of elements to take.

    Returns:
        Stream[TSource, TSource]: [description]
    """

    if count < 0:
        raise ValueError("Count cannot be negative.")

    def _take(source: AsyncObservable[TSource]) -> AsyncObservable[TSource]:
        async def subscribe_async(obvAsync: AsyncObserver[TSource]) -> AsyncDisposable:
            safe_obv, auto_detach = auto_detach_observer(obvAsync)

            remaining = count

            async def asend(value: TSource) -> None:
                nonlocal remaining

                if remaining > 0:
                    remaining -= 1
                    await safe_obv.asend(value)
                    if not remaining:
                        await safe_obv.aclose()

            obv = AsyncAnonymousObserver(asend, safe_obv.athrow, safe_obv.aclose)
            return await pipe(obv, source.subscribe_async, auto_detach)

        return AsyncAnonymousObservable(subscribe_async)

    return _take


def take_last(count: int) -> Stream[TSource, TSource]:
    """Take last elements from stream.

    Returns a specified number of contiguous elements from the end of an
    observable sequence.

    Args:
        count: Number of elements to take.

    Returns:
        Stream[TSource, TSource]: [description]
    """

    def _take_last(source: AsyncObservable[TSource]) -> AsyncObservable[TSource]:
        async def subscribe_async(aobv: AsyncObserver[TSource]) -> AsyncDisposable:
            safe_obv, auto_detach = auto_detach_observer(aobv)
            queue: List[TSource] = []

            async def asend(value: TSource) -> None:
                queue.append(value)
                if len(queue) > count:
                    queue.pop(0)

            async def aclose() -> None:
                for item in queue:
                    await safe_obv.asend(item)
                await safe_obv.aclose()

            obv = AsyncAnonymousObserver(asend, safe_obv.athrow, aclose)
            return await pipe(obv, source.subscribe_async, auto_detach)

        return AsyncAnonymousObservable(subscribe_async)

    return _take_last


def take_until(other: AsyncObservable[TResult]) -> Stream[TSource, TSource]:
    """Take elements until other.

    Returns the values from the source observable sequence until the
    other observable sequence produces a value.

    Args:
        other: The other async observable

    Returns:
        Stream[TSource, TSource]: [description]
    """

    def _take_until(source: AsyncObservable[TSource]) -> AsyncObservable[TSource]:
        async def subscribe_async(aobv: AsyncObserver[TSource]) -> AsyncDisposable:
            safe_obv, auto_detach = auto_detach_observer(aobv)

            async def asend(value: TSource) -> None:
                await safe_obv.aclose()

            obv = AsyncAnonymousObserver(asend, safe_obv.athrow)
            sub2 = await pipe(obv, other.subscribe_async)
            sub1 = await pipe(safe_obv, source.subscribe_async, auto_detach)

            return AsyncDisposable.composite(sub1, sub2)

        return AsyncAnonymousObservable(subscribe_async)

    return _take_until


def slice(start: Optional[int] = None, stop: Optional[int] = None, step: int = 1) -> Stream[TSource, TSource]:
    """Slices the given source stream.

    It is basically a wrapper around skip(), skip_last(), take(),
    take_last() and filter().
    This marble diagram helps you remember how slices works with
    streams. Positive numbers is relative to the start of the events,
    while negative numbers are relative to the end (on_completed) of the
    stream.

    ```
     r---e---a---c---t---i---v---e---|
     0   1   2   3   4   5   6   7   8
    -8  -7  -6  -5  -4  -3  -2  -1
    ```

    Example:
    >>> result = slice(1, 10, source)
    >>> result = slice(1, -2, source)
    >>> result = slice(1, -1, 2, source)

    Args:
        start: Number of elements to skip of take last
        stop: Last element to take of skip last
        step: Takes every step element. Must be larger than zero

    Returns:
        A sliced source stream.
    """

    def _slice(source: AsyncObservable[TSource]) -> AsyncObservable[TSource]:
        nonlocal start

        if start is not None:
            if start < 0:
                source = pipe(source, take_last(abs(start)))
            else:
                source = pipe(source, skip(start))

        if stop is not None:
            if stop > 0:
                start = start or 0
                source = pipe(source, take(stop - start))
            else:
                source = pipe(source, skip_last(abs(stop)))

        if step is not None:
            if step > 1:
                mapper: Callable[[Any, int], bool] = lambda _, i: i % step == 0
                source = pipe(source, filteri(mapper))
            elif step < 0:
                # Reversing streams is not supported
                raise TypeError("Negative step not supported.")

        return source

    return _slice
