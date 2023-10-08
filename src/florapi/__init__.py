"""florapi - A personal toolkit for building FastAPI applications."""

__author__ = "Richard Si"
__version__ = "2023.10.7"

import itertools
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import TypeVar

T = TypeVar("T")


def flatten(iterables: Iterable[Iterable[T]]) -> list[T]:
    """Flatten nested iterables into a single list."""
    return list(itertools.chain.from_iterable(iterables))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
