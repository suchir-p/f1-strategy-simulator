from __future__ import annotations

from collections.abc import Iterator
import threading
from dataclasses import dataclass, field

from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.database.session import Database
from app.workers.dispatcher import Dispatcher

if TYPE_CHECKING:
    from app.services.ratelimit import RateLimiter


@dataclass
class AppState:
    settings: Settings
    db: Database
    dispatcher: Dispatcher | None
    engine_version: str
    data_version: str
    trace_slots: threading.BoundedSemaphore = field(
        default_factory=lambda: threading.BoundedSemaphore(2))
    rate_limiter: "RateLimiter" = field(default_factory=lambda: _limiter())


def _limiter():
    from app.services.ratelimit import RateLimiter
    return RateLimiter()


def state(request: Request) -> AppState:
    return request.app.state.f1sim


def session(request: Request) -> Iterator[Session]:
    with state(request).db.session() as s:
        yield s
