"""Scheduler: when a fiber yields, and where its queued steps run.

    provide(MaxOpsBeforeYield.context(256))(program)       # yield more often
    provide(Scheduler.context(MixedScheduler("sync")))(p)  # replace the scheduler
    with_dispatcher(lambda d: sync(lambda: d.flush()))      # reach the fiber's dispatcher

All three are References, so using them never adds to R. The kernel (fiber.py) reads them by
key on every context change. Effect v4: Scheduler.ts, internal/effect.ts:567-570, :794-828.
"""

from __future__ import annotations

from .context import Reference
from .fiber import (
    DEFAULT_SCHEDULER,
    MixedScheduler,
    SchedulerDispatcher,
    SchedulerService,
    with_dispatcher,
)


class Scheduler(Reference[SchedulerService], key="effect/Scheduler"):
    """The fiber's scheduler. Default: the shared MixedScheduler (Scheduler.ts:82-85)."""

    @classmethod
    def default(cls) -> SchedulerService:
        return DEFAULT_SCHEDULER


class MaxOpsBeforeYield(Reference[int], key="effect/Scheduler/MaxOpsBeforeYield"):
    """Ops a fiber runs before MixedScheduler yields it. Default 2048 (Scheduler.ts:294-297)."""

    @classmethod
    def default(cls) -> int:
        return 2048


class PreventSchedulerYield(Reference[bool], key="effect/Scheduler/PreventSchedulerYield"):
    """When true, the run loop never asks should_yield. Default False (Scheduler.ts:321-324)."""

    @classmethod
    def default(cls) -> bool:
        return False


__all__ = [
    "MaxOpsBeforeYield",
    "MixedScheduler",
    "PreventSchedulerYield",
    "Scheduler",
    "SchedulerDispatcher",
    "SchedulerService",
    "with_dispatcher",
]
