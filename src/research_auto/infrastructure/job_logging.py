from __future__ import annotations

import contextvars
import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True, slots=True)
class JobLogContext:
    job_id: str
    job_type: str
    attempt_id: str | None = None
    worker_id: str | None = None
    payload: dict[str, Any] | None = None


_current_job_log_context: contextvars.ContextVar[JobLogContext | None] = contextvars.ContextVar(
    "current_job_log_context",
    default=None,
)


@contextmanager
def job_logging_context(**kwargs: Any) -> Iterator[None]:
    token = _current_job_log_context.set(JobLogContext(**kwargs))
    try:
        yield
    finally:
        _current_job_log_context.reset(token)


def get_job_log_context() -> JobLogContext | None:
    return _current_job_log_context.get()


def format_job_log_context() -> str:
    context = get_job_log_context()
    if context is None:
        return ""
    parts = [
        f"job_id={context.job_id}",
        f"job_type={context.job_type}",
    ]
    if context.attempt_id is not None:
        parts.append(f"attempt_id={context.attempt_id}")
    if context.worker_id is not None:
        parts.append(f"worker_id={context.worker_id}")
    if context.payload is not None:
        parts.append(f"payload={json.dumps(context.payload, sort_keys=True, separators=(',', ':'), default=str)}")
    return " ".join(parts)


def adapter_log_message(adapter: str, event: str, **fields: Any) -> str:
    parts = [f"adapter={adapter}", f"event={event}"]
    context = format_job_log_context()
    if context:
        parts.append(context)
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    return " ".join(parts)
