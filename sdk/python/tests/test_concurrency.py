"""Multi-thread and multi-worker behaviour (gunicorn/uvicorn fork model)."""

from __future__ import annotations

import os
import threading

import pytest

from ptaas_bench_sdk import BenchClient, config_from_env


def test_concurrent_threads_lose_nothing(collector):
    config = config_from_env(app="testapp", collector_url=collector.url, enabled=True, flush_interval=0.02)
    client = BenchClient(config)
    try:
        def worker(index: int) -> None:
            for step in range(50):
                client.note(f"{index}:{step}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert client.flush(timeout=10.0)
        events = collector.wait_for(1000, timeout=10.0)
        assert len({e["message"] for e in events}) == 1000
        stats = client.stats()
        assert stats["enqueued"] == 1000 and stats["dropped"] == 0
    finally:
        client.close(1.0)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork-based workers only")
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_each_forked_worker_flushes_independently(collector):
    """gunicorn/uvicorn fork after the app module is imported.

    The child inherits a queue snapshot and a flusher thread that does not exist any
    more. It must rebuild its own thread, flush on its own, and *not* resend what the
    parent had queued at fork time -- duplicated events would double-count in scoring.
    """
    config = config_from_env(
        app="testapp", collector_url=collector.url, enabled=True, flush_interval=30.0
    )
    client = BenchClient(config)
    try:
        client.note("queued-before-fork")  # still in the parent's queue when we fork

        pid = os.fork()
        if pid == 0:  # child: never return into pytest
            code = 0
            try:
                client.note("from-child")
                code = 0 if client.flush(5.0) else 1
            except BaseException:  # noqa: BLE001
                code = 2
            os._exit(code)

        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0

        client.note("from-parent")
        assert client.flush(timeout=10.0)
        messages = [e["message"] for e in collector.wait_for(3, timeout=5.0)]
        assert {"queued-before-fork", "from-child", "from-parent"} <= set(messages)
        assert messages.count("queued-before-fork") == 1
    finally:
        client.close(1.0)
