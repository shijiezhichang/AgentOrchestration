import asyncio
import pytest
from src.agent.retry import (
    RetryRuntime,
    RetryConfig,
    RunState,
)


# ── Helpers ──

async def _success_handler(task):
    return {"status": "ok", "data": task.get("input")}


async def _flaky_handler(fail_count: int):
    """Returns a handler that fails `fail_count` times then succeeds."""
    calls = [0]

    async def handler(task):
        calls[0] += 1
        if calls[0] <= fail_count:
            raise RuntimeError(f"Simulated failure #{calls[0]}")
        return {"status": "ok", "call": calls[0]}

    return handler


# ── RetryConfig Tests ──

class TestRetryConfig:

    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.base_delay == 1.0
        assert cfg.max_delay == 30.0
        assert cfg.backoff_factor == 2.0

    def test_delay_for_attempt(self):
        cfg = RetryConfig(base_delay=1.0, backoff_factor=2.0)
        assert cfg.delay_for_attempt(0) == 1.0
        assert cfg.delay_for_attempt(1) == 2.0
        assert cfg.delay_for_attempt(2) == 4.0
        assert cfg.delay_for_attempt(3) == 8.0

    def test_delay_capped_at_max(self):
        cfg = RetryConfig(base_delay=10, max_delay=15, backoff_factor=2.0)
        assert cfg.delay_for_attempt(1) == 15.0  # 10*2=20 capped at 15

    def test_custom_config(self):
        cfg = RetryConfig(max_retries=5, base_delay=0.5, max_delay=10.0)
        assert cfg.max_retries == 5

    def test_negative_max_retries_raises(self):
        with pytest.raises(ValueError, match=">= 0"):
            RetryConfig(max_retries=-1)


# ── RunState Tests ──

class TestRunState:

    def test_terminal_states(self):
        assert RunState.COMPLETED.is_terminal is True
        assert RunState.CANCELLED.is_terminal is True

    def test_non_terminal_states(self):
        assert RunState.PENDING.is_terminal is False
        assert RunState.RUNNING.is_terminal is False
        assert RunState.FAILED.is_terminal is False


# ── RetryRuntime Tests ──

class TestRetryRuntime:

    @pytest.mark.asyncio
    async def test_successful_execution_no_retry(self):
        rt = RetryRuntime()
        result = await rt.execute("run-1", {"input": "hello"}, _success_handler)
        assert result["outcome"] == "completed"
        assert result["result"]["status"] == "ok"
        assert result["attempts"] == 1
        assert rt.get_state("run-1") == RunState.COMPLETED
        assert rt.is_terminal("run-1")

    @pytest.mark.asyncio
    async def test_retry_then_succeed(self):
        rt = RetryRuntime(RetryConfig(max_retries=3, base_delay=0.01))
        handler = await _flaky_handler(2)  # fails twice, succeeds on 3rd
        result = await rt.execute("run-2", {}, handler)
        assert result["outcome"] == "completed"
        assert result["attempts"] == 3  # 2 fails + 1 success
        assert result["result"]["call"] == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        rt = RetryRuntime(RetryConfig(max_retries=2, base_delay=0.01))
        handler = await _flaky_handler(5)  # fails 5 times, max_retries=2
        result = await rt.execute("run-3", {}, handler)
        assert result["outcome"] == "failed"
        assert result["attempts"] == 3  # original + 2 retries
        assert result["error"] is not None
        assert rt.get_state("run-3") == RunState.FAILED
        assert not rt.is_terminal("run-3")  # FAILED is not terminal for this state machine

    @pytest.mark.asyncio
    async def test_cancel_stops_retry_loop(self):
        """Cancelling a run must stop the retry loop immediately."""
        rt = RetryRuntime(RetryConfig(max_retries=10, base_delay=0.1))

        async def slow_failer(task):
            await asyncio.sleep(10)  # never finishes

        async def cancel_after_delay():
            await asyncio.sleep(0.05)
            rt.cancel("run-cancel")

        # Start execution then cancel
        task = asyncio.create_task(rt.execute("run-cancel", {}, slow_failer))
        cancel_task = asyncio.create_task(cancel_after_delay())

        done, pending = await asyncio.wait(
            [task, cancel_task],
            timeout=2.0,
            return_when=asyncio.ALL_COMPLETED,
        )

        # Cancel didn't propagate as CancelledError to the main task
        # because cancel() just sets state; the loop should detect it
        for t in pending:
            t.cancel()

        # After cancel, the run should be CANCELLED (terminal)
        assert rt.is_terminal("run-cancel")
        assert rt.get_state("run-cancel") == RunState.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_already_terminal_noop(self):
        rt = RetryRuntime()
        await rt.execute("run-x", {}, _success_handler)
        assert rt.is_terminal("run-x")
        # Cancelling an already terminal run should return False
        assert rt.cancel("run-x") is False

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_run(self):
        rt = RetryRuntime()
        assert rt.cancel("nonexistent") is False

    @pytest.mark.asyncio
    async def test_idempotency_key_prevents_duplicate(self):
        """Same idempotency key returns cached result without re-executing."""
        rt = RetryRuntime()
        calls = []

        async def counting_handler(task):
            calls.append(1)
            return {"done": True}

        # First execution
        r1 = await rt.execute("run-a", {}, counting_handler, idempotency_key="key-1")
        assert r1["outcome"] == "completed"
        assert len(calls) == 1

        # Second execution with same key — must return cached, no handler call
        r2 = await rt.execute("run-b", {}, counting_handler, idempotency_key="key-1")
        assert r2["outcome"] == "completed"
        assert len(calls) == 1  # NOT called again

    @pytest.mark.asyncio
    async def test_idempotency_no_key_no_cache(self):
        """Without idempotency key, each run executes independently."""
        rt = RetryRuntime()
        calls = []

        async def counting_handler(task):
            calls.append(1)
            return {"done": True}

        await rt.execute("run-1", {}, counting_handler)
        await rt.execute("run-2", {}, counting_handler)
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_zero_retries_fails_immediately(self):
        rt = RetryRuntime(RetryConfig(max_retries=0, base_delay=0.01))

        async def always_fail(task):
            raise ValueError("boom")

        result = await rt.execute("run-fail", {}, always_fail)
        assert result["outcome"] == "failed"
        assert result["attempts"] == 1  # original attempt only

    @pytest.mark.asyncio
    async def test_result_persisted_on_failure(self):
        """Even after failure, result is durably stored."""
        rt = RetryRuntime(RetryConfig(max_retries=1, base_delay=0.01))

        async def fail(task):
            raise RuntimeError("persistent failure")

        await rt.execute("run-rec", {}, fail)
        stored = rt.get_result("run-rec")
        assert stored is not None
        assert stored["outcome"] == "failed"
        assert stored["error"] == "persistent failure"

    @pytest.mark.asyncio
    async def test_result_persisted_on_success(self):
        rt = RetryRuntime()
        await rt.execute("run-ok", {"input": 1}, _success_handler)
        stored = rt.get_result("run-ok")
        assert stored["outcome"] == "completed"
        assert stored["result"]["data"] == 1

    @pytest.mark.asyncio
    async def test_get_state_unknown_run(self):
        rt = RetryRuntime()
        assert rt.get_state("ghost") is None
        assert rt.get_result("ghost") is None
        assert rt.is_terminal("ghost") is False

    @pytest.mark.asyncio
    async def test_concurrent_executions_independent(self):
        """Concurrent runs don't interfere with each other."""
        rt = RetryRuntime()

        async def delayed_success(task):
            delay = task.get("delay", 0.01)
            await asyncio.sleep(delay)
            return {"id": task["id"]}

        tasks = [
            rt.execute(f"run-{i}", {"id": i, "delay": 0.02 * i}, delayed_success)
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)

        for i, r in enumerate(results):
            assert r["outcome"] == "completed"
            assert r["result"]["id"] == i

    @pytest.mark.asyncio
    async def test_state_transitions_completed(self):
        rt = RetryRuntime()
        assert rt.get_state("run-s") is None
        await rt.execute("run-s", {}, _success_handler)
        assert rt.get_state("run-s") == RunState.COMPLETED

    @pytest.mark.asyncio
    async def test_state_transitions_failed(self):
        rt = RetryRuntime(RetryConfig(max_retries=0))

        async def fail(task):
            raise RuntimeError("fail")

        await rt.execute("run-f", {}, fail)
        assert rt.get_state("run-f") == RunState.FAILED

    @pytest.mark.asyncio
    async def test_exponential_backoff_applied(self):
        """Retries should wait with exponential backoff."""
        rt = RetryRuntime(RetryConfig(max_retries=3, base_delay=0.01, backoff_factor=2.0))
        handler = await _flaky_handler(2)

        start = asyncio.get_event_loop().time()
        result = await rt.execute("run-backoff", {}, handler)
        elapsed = asyncio.get_event_loop().time() - start

        assert result["outcome"] == "completed"
        # 2 failures: delays of 0.01 + 0.02 = 0.03s minimum
        assert elapsed >= 0.02
