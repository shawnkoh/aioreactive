from abc import abstractmethod
from typing import Awaitable, Callable, Generic, Optional, Protocol, TypeVar, Union

from expression.system import AsyncDisposable

_T = TypeVar("_T")
_TSource = TypeVar("_TSource")
_T_out = TypeVar("_T_out", covariant=True)  # Any type covariant containers.
_T_in = TypeVar("_T_in", contravariant=True)  # Ditto contravariant.

SendAsync = Callable[[_T], Awaitable[None]]
ThrowAsync = Callable[[Exception], Awaitable[None]]
CloseAsync = Callable[[], Awaitable[None]]


class AsyncObserver(Generic[_T_in]):
    """An asynchronous observable."""

    __slots__ = ()

    @abstractmethod
    async def asend(self, value: _T_in) -> None:
        raise NotImplementedError

    @abstractmethod
    async def athrow(self, error: Exception) -> None:
        raise NotImplementedError

    @abstractmethod
    async def aclose(self) -> None:
        raise NotImplementedError


class AsyncObservable(Generic[_T_out]):
    __slots__ = ()

    @abstractmethod
    async def subscribe_async(
        self,
        send: Optional[Union[SendAsync[_T_out], AsyncObserver[_T_out]]] = None,
        throw: Optional[ThrowAsync] = None,
        close: Optional[CloseAsync] = None,
    ) -> AsyncDisposable:
        raise NotImplementedError


class Flatten(Protocol):
    """A zipping projetion is a function that projects from one observable to a zipped, i.e:

    `AsyncObservable[AsyncObservable[TSource]]) -> AsyncObservable[Tuple[TSource, TResult]]`
    """

    def __call__(
        self, __source: AsyncObservable[AsyncObservable[_TSource]]
    ) -> AsyncObservable[_TSource]:
        raise NotImplementedError


__all__ = ["AsyncObserver", "AsyncObservable"]
