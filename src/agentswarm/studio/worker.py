"""Steps that take minutes (finding sources, writing, reviewing) run in the background, so a page answers at
once and shows the run working; an error is kept for the page to show."""

import threading
from collections.abc import Callable
from typing import Protocol


class Worker(Protocol):
    def submit(self, name: str, label: str, work: Callable[[], object]) -> None:
        """Starts `work` for run `name`; raises ValueError while that run is busy already."""
        ...

    def busy(self, name: str) -> str | None:
        """What the run is doing now ("writing"), or None."""
        ...

    def error(self, name: str) -> str | None:
        """The last step's error, until the next step starts."""
        ...


class ThreadWorker:
    """One thread per step, one step at a time per run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy: dict[str, str] = {}
        self._errors: dict[str, str] = {}

    def submit(self, name: str, label: str, work: Callable[[], object]) -> None:
        with self._lock:
            if name in self._busy:
                raise ValueError(f"{name} is busy {self._busy[name]}")
            self._busy[name] = label
            self._errors.pop(name, None)
        threading.Thread(target=self.run, args=(name, work), daemon=True, name=f"run-{name}").start()

    def run(self, name: str, work: Callable[[], object]) -> None:
        try:
            work()
        except Exception as error:  # the pipeline keeps the run; the page shows why and offers a retry
            with self._lock:
                self._errors[name] = f"{type(error).__name__}: {error}"
        finally:
            with self._lock:
                self._busy.pop(name, None)

    def busy(self, name: str) -> str | None:
        with self._lock:
            return self._busy.get(name)

    def error(self, name: str) -> str | None:
        with self._lock:
            return self._errors.get(name)
