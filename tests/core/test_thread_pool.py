"""Smoke test for app.core.thread_pool's module-level executor."""

import os
from concurrent.futures import ThreadPoolExecutor

from app.core import thread_pool


def test_executor_is_a_thread_pool_executor():
    assert isinstance(thread_pool.executor, ThreadPoolExecutor)


def test_max_workers_is_bounded_and_positive():
    assert 1 <= thread_pool.max_workers <= 32
    assert thread_pool.max_workers == min(32, os.cpu_count() * 4)


def test_executor_runs_submitted_work():
    future = thread_pool.executor.submit(lambda: 1 + 1)
    assert future.result(timeout=5) == 2
