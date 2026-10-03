"""The scheduler: shared resources across every project in a process (proposal §14.2).

It holds three resources and makes no judgements about content:

- **model calls:** at most `max_model_calls` team actions run at once, across all projects. Each project's
  loop asks for at most one grant at a time and waiters are served in arrival order, so a busy project
  can't starve a quiet one;
- **Jev requests:** at most `jev_per_minute`, a sliding window shared by every decider;
- **sandbox runs:** at most `max_sandboxes` containers (or host processes) at once.

Budgets are per project and live in `control/budget.py`: a project over its budget doesn't ask for grants.
"""

import asyncio
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agentswarm.core.domain import ProjectId
from agentswarm.workspace.sandbox import Sandbox, SandboxResult


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_model_calls: int = Field(default=4, ge=1)
    jev_per_minute: int = Field(default=1200, ge=1)
    max_sandboxes: int = Field(default=4, ge=1)


class RateLimiter:
    """At most `per_minute` acquisitions in any 60 seconds; acquire() waits for room. Thread-safe."""

    WINDOW_S = 60.0

    def __init__(
        self,
        per_minute: int,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._per_minute = per_minute
        self._now = now
        self._sleep = sleep
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._now()
                while self._times and now - self._times[0] >= self.WINDOW_S:
                    self._times.popleft()
                if len(self._times) < self._per_minute:
                    self._times.append(now)
                    return
                wait = self.WINDOW_S - (now - self._times[0])
            self._sleep(wait)


class ThrottledSandbox:
    """A sandbox that waits for one of the scheduler's slots before each run."""

    def __init__(self, sandbox: Sandbox, slots: threading.BoundedSemaphore) -> None:
        self._sandbox = sandbox
        self._slots = slots

    def run(self, command: Sequence[str], cwd: Path, timeout_s: float) -> SandboxResult:
        with self._slots:
            return self._sandbox.run(command, cwd, timeout_s)


class Scheduler:
    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or SchedulerConfig()
        self._model_calls = asyncio.Semaphore(self.config.max_model_calls)
        self.jev = RateLimiter(self.config.jev_per_minute)
        self._sandbox_slots = threading.BoundedSemaphore(self.config.max_sandboxes)
        self.granted: dict[ProjectId, int] = {}  # model-call grants per project, for reports and tests

    @asynccontextmanager
    async def model_call(self, project: ProjectId) -> AsyncGenerator[None]:
        """Hold one model-call slot while a team acts."""
        async with self._model_calls:
            self.granted[project] = self.granted.get(project, 0) + 1
            yield

    def throttle(self, sandbox: Sandbox) -> Sandbox:
        return ThrottledSandbox(sandbox, self._sandbox_slots)
