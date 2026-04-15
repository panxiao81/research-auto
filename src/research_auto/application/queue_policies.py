from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QueuePolicy:
    name: str
    job_types: tuple[str, ...]
    base_retry_seconds: int = 30
    rate_limit_retry_seconds: int = 300
    max_running_jobs: int | None = None
    min_start_interval_seconds: int = 0

    def retry_delay_seconds(self, *, attempt_count: int, error_message: str) -> int:
        if is_rate_limit_error(error_message):
            return max(
                self.rate_limit_retry_seconds,
                attempt_count * self.rate_limit_retry_seconds,
            )
        return max(self.base_retry_seconds, attempt_count * self.base_retry_seconds)


QUEUE_POLICIES = {
    "all": QueuePolicy(
        name="all",
        job_types=(
            "crawl_track",
            "resolve_paper_artifacts",
            "download_artifact",
            "parse_artifact",
            "summarize_paper",
        ),
    ),
    "crawl": QueuePolicy(
        name="crawl",
        job_types=("crawl_track",),
        base_retry_seconds=60,
        rate_limit_retry_seconds=300,
    ),
    "resolve": QueuePolicy(
        name="resolve",
        job_types=("resolve_paper_artifacts",),
        base_retry_seconds=60,
        rate_limit_retry_seconds=300,
        max_running_jobs=1,
        min_start_interval_seconds=3,
    ),
    "download": QueuePolicy(
        name="download",
        job_types=("download_artifact",),
        base_retry_seconds=60,
        rate_limit_retry_seconds=180,
    ),
    "parse": QueuePolicy(
        name="parse",
        job_types=("parse_artifact",),
        base_retry_seconds=30,
        rate_limit_retry_seconds=120,
    ),
    "llm": QueuePolicy(
        name="llm",
        job_types=("summarize_paper",),
        base_retry_seconds=30,
        rate_limit_retry_seconds=300,
    ),
}


def get_queue_policy(queue_name: str) -> QueuePolicy:
    policy = QUEUE_POLICIES.get(queue_name)
    if policy is None:
        raise ValueError(f"unsupported worker queue: {queue_name}")
    return policy


def is_rate_limit_error(message: str) -> bool:
    lowered = message.lower()
    return "429" in lowered or "rate limit" in lowered or "too many requests" in lowered
