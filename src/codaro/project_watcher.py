"""Debounced project changes proactively refresh derived data, never source files.

Queries still perform their ordinary freshness/policy checks. The watcher is an
optimization, not an authorization cache or a guarantee that events cannot be lost.
"""

import threading
import time
from pathlib import Path

from codaro.index import CodeIndex
from codaro.repository import EXCLUDED_DIRS


class ProjectWatcher:
    def __init__(self, repository, stop, *, reconcile_seconds=300):
        self.repository, self.stop = repository, stop
        self.reconcile_seconds = reconcile_seconds
        self.thread = None
        self.ready = threading.Event()
        self.updated = threading.Event()
        self.status = "idle"
        self.batches = 0
        self.last_error = ""

    def relevant(self, change, name):
        try:
            relative = Path(name).relative_to(self.repository.root)
            return not any(part in EXCLUDED_DIRS for part in relative.parts)
        except ValueError:
            return False

    def start(self):
        if self.thread is not None or self.stop.is_set():
            return
        self.thread = threading.Thread(target=self.run, daemon=True, name="codaro-project-watch")
        self.thread.start()

    def run(self):
        try:
            from watchfiles import watch

            self.status = "watching"
            reconciled = time.monotonic()
            for changes in watch(
                self.repository.root,
                watch_filter=self.relevant,
                stop_event=self.stop,
                debounce=150,
                step=50,
                rust_timeout=200,
                yield_on_timeout=True,
            ):
                self.ready.set()
                if self.stop.is_set():
                    break
                full = time.monotonic() - reconciled >= self.reconcile_seconds
                if not changes and not full:
                    continue
                names = {
                    Path(name).relative_to(self.repository.root).as_posix() for _, name in changes
                }
                full = (
                    full
                    or len(names) > 4096
                    or any(
                        Path(name).name in {".gitignore", ".ignore", ".rgignore", ".codaroignore"}
                        for name in names
                    )
                )
                with CodeIndex(self.repository) as index:
                    index.update(cancelled=self.stop, changed_paths=None if full else names)
                if full:
                    reconciled = time.monotonic()
                self.batches += 1
                self.updated.set()
            self.status = "stopped"
        except Exception as exc:
            # Normal query-time updates remain available if native watching fails.
            self.status = "stopped" if self.stop.is_set() else "unavailable"
            self.last_error = type(exc).__name__
        finally:
            self.ready.set()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
