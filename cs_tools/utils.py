from __future__ import annotations

from base64 import (
    urlsafe_b64decode as b64d,
    urlsafe_b64encode as b64e,
)
from collections.abc import Awaitable, Coroutine, Generator, Iterable, Sequence
from contextvars import Context
from typing import Annotated, Any, TypeVar
import asyncio
import datetime as dt
import getpass
import importlib
import itertools as it
import logging
import pathlib
import sys
import threading
import zlib

from sqlalchemy import types as sa_types
from sqlalchemy.schema import Column
from sqlmodel import Field, SQLModel
import pydantic

log = logging.getLogger(__name__)
_T = TypeVar("_T")
_EVENT_LOOP: asyncio.AbstractEventLoop | None = None


def get_event_loop() -> asyncio.AbstractEventLoop:
    """Fetch an event loop."""
    # DEV NOTE: @boonhapus, 2024/11/24
    # IF WE WERE TO SWITCH TO thoughtspot.ThoughtSpot ACTING AS A GLOBAL ENTRYPOINT THEN
    # THIS FUNCTION WOULD BE NO LONGER NEEDED, AND WE COULD USE asyncio.run() INSTEAD.
    global _EVENT_LOOP

    # RETURN THE EVENT LOOP IF IT'S ALREADY BEEN SET FOR THE PROCESS.
    if _EVENT_LOOP is not None:
        return _EVENT_LOOP

    try:
        loop = asyncio.get_running_loop()

    except RuntimeError:
        loop = asyncio.new_event_loop()

        # SET THE EVENT LOOP ON THE THREAD.
        asyncio.set_event_loop(loop)

        # SET THE EVENT LOOP FOR THE PROCESS.
        _EVENT_LOOP = loop

    return loop


def run_sync(coro: Awaitable) -> Any:
    """Run a coroutine synchronously."""
    return get_event_loop().run_until_complete(coro)


class BoundedTaskGroup(asyncio.TaskGroup):
    """An asyncio.TaskGroup that implements backpressure."""

    def __init__(self, max_concurrent: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(value=max_concurrent)

    def create_task(self, coro: Coroutine, *, name: str | None = None, context: Context | None = None) -> asyncio.Task:
        """See: https://docs.python.org/3/library/asyncio-task.html#asyncio.TaskGroup.create_task"""

        async def with_backpressure() -> Any:
            async with self._semaphore:
                return await coro

        return super().create_task(coro=with_backpressure(), name=name, context=context)


async def bounded_gather(
    *aws: Awaitable,
    max_concurrent: int,
    return_exceptions: bool = False,
) -> Sequence[Any]:
    """An asyncio.gather that implements backpressure."""
    semaphore = asyncio.Semaphore(value=max_concurrent)

    async def with_backpressure(coro: Awaitable) -> Any:
        async with semaphore:
            return await coro

    return await asyncio.gather(*(with_backpressure(coro) for coro in aws), return_exceptions=return_exceptions)


def batched(iterable: Iterable[_T], *, n: int) -> Generator[Iterable[_T], None, None]:
    """Yield successive n-sized chunks from list."""
    # batched('ABCDEFG', 3) --> ABC DEF G
    if n < 1:
        raise ValueError("n must be at least one")

    iterable = iter(iterable)

    while batch := tuple(it.islice(iterable, n)):
        yield batch


def determine_editable_install(package_name: str = "cs_tools") -> bool:
    """Determine if the current CS Tools context is an editable install."""
    return any(f"__editable__.{package_name}" in path for path in sys.path)


def get_package_directory(package_name: str) -> pathlib.Path | None:
    """Get the path to the package directory."""
    try:
        module = importlib.import_module(package_name)
        assert module.__spec__ is not None
        assert module.__spec__.origin is not None

    except (ModuleNotFoundError, AssertionError):
        return None

    return pathlib.Path(module.__spec__.origin).parent


def obscure(data: bytes) -> bytes:
    """
    Encode data to obscure its text.

    This is security by obfuscation.
    """
    if data is None:
        return

    if isinstance(data, str):
        data = str.encode(data)

    return b64e(zlib.compress(data, level=9))


def reveal(obscured: bytes) -> bytes:
    """
    Decode obscured data to reveal its text.

    This is security by obfuscation.
    """
    if obscured is None:
        return
    return zlib.decompress(b64d(obscured))


def anonymize(text: str, *, anonymizer: str = " {anonymous} ") -> str:
    """Replace text with an anonymous value."""
    text = text.replace(getpass.getuser(), anonymizer)
    return text


class GlobalState:
    """
    An object that can be used to store arbitrary state.

    Access members with dotted access.

    >>> global_state = GlobalState()
    >>> global_state.foo = 'bar'
    >>> print(global_state.foo)
    bar
    >>> global_state.abc
    AttributeError: 'State' object has no attribute 'abc'
    """

    _state: dict[str, Any]

    def __init__(self, state: dict[str, Any] | None = None):
        if state is None:
            state = {}

        super().__setattr__("_state", state)

    def __setattr__(self, key: Any, value: Any) -> None:
        self._state[key] = value

    def __getattr__(self, key: Any) -> Any:
        try:
            return self._state[key]
        except KeyError:
            cls_name = self.__class__.__name__
            raise AttributeError(f"'{cls_name}' object has no attribute '{key}'") from None

    def __delattr__(self, key: Any) -> None:
        del self._state[key]


def timedelta_strftime(timedelta: dt.timedelta, *, sep: str = " ") -> str:
    """Convert a timedelta to an fstring HHH:MM:SS."""
    total_seconds = int(timedelta.total_seconds())
    H, r = divmod(total_seconds, 3600)
    M, S = divmod(r, 60)
    return f"{H: 3d}h{sep}{M:02d}m{sep}{S:02d}s"


def create_dynamic_model(__tablename__: str, *, sample_row: dict[str, Any]) -> type[SQLModel]:
    """Create a SQLModel from a sample data row."""
    SQLA_DATA_TYPES = {
        str: sa_types.Text,
        bool: sa_types.Boolean,
        int: sa_types.BigInteger,
        float: sa_types.Float,
        dt.date: sa_types.Date,
        dt.datetime: sa_types.DateTime,
    }

    field_definitions = {
        "cluster_guid": Annotated[str, Field(..., sa_column=Column(type_=sa_types.Text, primary_key=True))],
        "sk_dummy": Annotated[str, Field(..., sa_column=Column(type_=sa_types.Text, primary_key=True))],
    }

    for column_name, value in sample_row.items():
        if column_name in field_definitions:
            continue

        try:
            py_type = type(value)
            sa_type = SQLA_DATA_TYPES[py_type]
        except KeyError:
            log.warning(f"{__tablename__}.{column_name} found no data type for '{py_type}', faling back to VARCHAR..")
            sa_type = sa_types.Text

        field_definitions[column_name] = Annotated[py_type, Field(None, sa_column=Column(type_=sa_type))]

    # CREATE THE DYNAMIC TABLE
    Model = pydantic.create_model(__tablename__, __base__=SQLModel, __cls_kwargs__={"table": True}, **field_definitions)  # type: ignore[call-overload]

    return Model


class ExceptedThread(threading.Thread):
    """For background threads: if errors, use log.debug instead of log.warning."""

    def run(self) -> None:
        try:
            super().run()

        except Exception:
            log.debug(f"Something went wrong in {self}", exc_info=True)
