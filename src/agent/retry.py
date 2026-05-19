"""Retry Runtime — Bounded retry with terminal-state guard and idempotency."""

import asyncio
import time
import logging
from enum import Enum
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class RunState(Enum):
    """States for a run execution. Terminal states stop retries."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (RunState.COMPLETED, RunState.CANCELLED)


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.max_retries = max_retries
        self.max_attempts = max_retries + 1  # total attempts = retries + original
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor

    def delay_for_attempt(self, attempt: int) -> float:
        """Exponential backoff: base_delay * backoff_factor^attempt, capped at max_delay."""
        d = self.base_delay * (self.backoff_factor ** attempt)
        return min(d, self.max_delay)


class RetryRuntime:
    """Orchestrates task execution with bounded, idempotent retries.

    Guards against retrying after terminal states (COMPLETED, CANCELLED).
    Persists durable state before emitting side effects.
    Uses idempotency keys to prevent duplicate execution.
    """

    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()
        self._states: Dict[str, RunState] = {}
        self._results: Dict[str, Dict[str, Any]] = {}
        self._idempotency_keys: Dict[str, str] = {}
        self._attempts: Dict[str, int] = {}

    async def execute(
        self,
        run_id: str,
        task: Dict[str, Any],
        handler: Callable,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a task with retries, respecting terminal state guard.

        Returns the durable result record.
        """
        # Idempotency check: if this exact key was already processed, return cached result
        if idempotency_key and idempotency_key in self._idempotency_keys:
            existing_run_id = self._idempotency_keys[idempotency_key]
            cached = self._results.get(existing_run_id)
            if cached:
                logger.debug(f"Idempotent return for key={idempotency_key}")
                return cached

        # Start or resume the run
        self._states[run_id] = RunState.RUNNING
        if idempotency_key:
            self._idempotency_keys[idempotency_key] = run_id

        last_error = None
        attempt = self._attempts.get(run_id, 0)
        total_attempts = attempt  # track actual calls made

        while attempt < self.config.max_attempts:
            # Guard: stop if terminal state reached (e.g. cancelled externally)
            state = self._states.get(run_id)
            if state and state.is_terminal:
                logger.info(f"Run {run_id} is terminal ({state.value}), stopping retry")
                break

            total_attempts += 1
            try:
                result = await handler(task)
                # Persist durable state BEFORE emitting side effects
                durable = self._build_result(run_id, total_attempts, "completed", result)
                self._results[run_id] = durable
                self._states[run_id] = RunState.COMPLETED
                self._attempts[run_id] = attempt
                return durable

            except asyncio.CancelledError:
                self._states[run_id] = RunState.CANCELLED
                durable = self._build_result(run_id, total_attempts, "cancelled")
                self._results[run_id] = durable
                raise

            except Exception as e:
                last_error = e
                attempt += 1
                self._attempts[run_id] = attempt

                if attempt >= self.config.max_attempts:
                    logger.error(
                        f"Run {run_id} exhausted {self.config.max_retries} retries: {e}"
                    )
                    break

                delay = self.config.delay_for_attempt(attempt - 1)
                logger.warning(
                    f"Run {run_id} attempt {attempt}/{self.config.max_attempts} "
                    f"failed: {e}. Retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)

        # All retries exhausted or terminal state reached
        self._states[run_id] = RunState.FAILED
        durable = self._build_result(run_id, total_attempts, "failed", error=str(last_error))
        self._results[run_id] = durable
        return durable

    def cancel(self, run_id: str) -> bool:
        """Cancel a run — moves it to terminal CANCELLED state."""
        if run_id not in self._states:
            return False
        current = self._states[run_id]
        if current.is_terminal:
            return False  # Already terminal
        self._states[run_id] = RunState.CANCELLED
        durable = self._build_result(run_id, self._attempts.get(run_id, 0), "cancelled")
        self._results[run_id] = durable
        return True

    def get_state(self, run_id: str) -> Optional[RunState]:
        return self._states.get(run_id)

    def get_result(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._results.get(run_id)

    def is_terminal(self, run_id: str) -> bool:
        state = self._states.get(run_id)
        return state is not None and state.is_terminal

    def _build_result(
        self,
        run_id: str,
        attempt: int,
        outcome: str,
        result: Any = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = {
            "run_id": run_id,
            "attempts": attempt,
            "outcome": outcome,
            "completed_at": time.time(),
        }
        if result is not None:
            record["result"] = result
        if error is not None:
            record["error"] = error
        return record
