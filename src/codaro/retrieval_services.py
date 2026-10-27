"""Session-owned language servers and reusable local embeddings with bounded warm-up."""

from __future__ import annotations

import contextvars
import threading
from collections import OrderedDict

from codaro.rpc import StdioRPC


class LanguageServerPool:
    def __init__(self):
        self.clients = OrderedDict()
        self.lock = threading.RLock()

    def acquire(self, argv, root):
        if not self.lock.acquire(timeout=5):
            raise ValueError("Servidor de linguagem ocupado.")
        try:
            key = (str(root), tuple(argv))
            client = self.clients.get(key)
            if client is None or client.closed:
                client = StdioRPC(argv, root, framing="headers")
                self.clients[key] = client
            self.clients.move_to_end(key)
            while len(self.clients) > 8:
                _, oldest = self.clients.popitem(last=False)
                oldest.close()
            return client
        except BaseException:
            self.lock.release()
            raise

    def release(self, client):
        self.lock.release()

    def close(self):
        for client in list(self.clients.values()):
            try:
                client.close()
            except (OSError, ValueError, TimeoutError):
                pass
        self.clients.clear()


class RetrievalServices:
    def __init__(self, repository):
        self.repository = repository
        self.lsp = LanguageServerPool()
        self._semantic = None
        self.stop = threading.Event()
        self.worker = None
        self.warmup_status = "idle"
        self.watcher = None

    def start_watch(self):
        from codaro.project_watcher import ProjectWatcher

        if self.watcher is None and not self.stop.is_set():
            self.watcher = ProjectWatcher(self.repository, self.stop)
            self.watcher.start()

    def semantic(self):
        if self._semantic is None:
            from codaro.semantic import SemanticSearch

            self._semantic = SemanticSearch()
        return self._semantic

    def warm(self, filters):
        if self.worker and self.worker.is_alive() or self.stop.is_set():
            return
        backend = self.semantic()
        if backend.status not in {"partial_index_warming", "scope_limited"}:
            return
        owner = self

        class WarmSearch:
            @property
            def status(self):
                return backend.status

            def search(self, *args, **kwargs):
                return backend.search(*args, **kwargs, warm_limit=32, warm_only=True)

        def run():
            from codaro.index import CodeIndex
            from codaro.runtime import request_deadline
            from codaro.trace import current_flow

            current_flow.set(None)
            request_deadline.set(None)
            owner.warmup_status = "running"
            try:
                with CodeIndex(owner.repository) as index:
                    index.cancelled, index.semantic = owner.stop, WarmSearch()
                    for _ in range(4):
                        if owner.stop.is_set():
                            break
                        index.search("code", 1, **filters)
                        if backend.status == "ready":
                            break
                owner.warmup_status = "cancelled" if owner.stop.is_set() else backend.status
            except Exception:
                owner.warmup_status = "interrupted"

        context = contextvars.copy_context()
        self.worker = threading.Thread(
            target=lambda: context.run(run), daemon=True, name="codaro-index-warmup"
        )
        self.worker.start()

    def close(self):
        self.stop.set()
        self.lsp.close()
        if self.worker:
            self.worker.join(timeout=3)
        if self.watcher:
            self.watcher.close()
