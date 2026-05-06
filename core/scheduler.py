"""
Scheduler — runs automated cycles (daily standups, forecasts, market scans)
on configurable intervals without any user interaction required.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, time
from typing import Callable, Optional

logger = logging.getLogger("scheduler")


class ScheduledJob:
    def __init__(self, name: str, coro_factory: Callable, interval_seconds: int):
        self.name = name
        self.coro_factory = coro_factory
        self.interval_seconds = interval_seconds
        self.last_run: Optional[datetime] = None
        self.run_count = 0
        self.error_count = 0


class Scheduler:
    """
    Lightweight async scheduler that runs background jobs at fixed intervals.
    Each job is a coroutine factory (callable returning a coroutine).
    """

    def __init__(self):
        self._jobs: list[ScheduledJob] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def add_job(self, name: str, coro_factory: Callable, interval_seconds: int) -> None:
        self._jobs.append(ScheduledJob(name, coro_factory, interval_seconds))
        logger.info(f"Scheduled: {name} every {interval_seconds}s")

    async def _run_job(self, job: ScheduledJob) -> None:
        try:
            await job.coro_factory()
            job.last_run = datetime.utcnow()
            job.run_count += 1
            logger.info(f"Job '{job.name}' completed (run #{job.run_count})")
        except Exception as e:
            job.error_count += 1
            logger.error(f"Job '{job.name}' failed: {e}")

    async def _loop(self) -> None:
        # Track next run time per job
        next_run = {job.name: asyncio.get_event_loop().time() + job.interval_seconds
                    for job in self._jobs}
        while self._running:
            now = asyncio.get_event_loop().time()
            for job in self._jobs:
                if now >= next_run[job.name]:
                    asyncio.create_task(self._run_job(job))
                    next_run[job.name] = now + job.interval_seconds
            await asyncio.sleep(10)  # check every 10s

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Scheduler started with {len(self._jobs)} jobs")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    def status(self) -> list[dict]:
        return [
            {
                "name": j.name,
                "interval_seconds": j.interval_seconds,
                "last_run": j.last_run.isoformat() if j.last_run else None,
                "run_count": j.run_count,
                "error_count": j.error_count,
            }
            for j in self._jobs
        ]
