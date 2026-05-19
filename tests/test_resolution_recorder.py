import pytest
import time
from src.agent.registry import (
    ResolutionRecorder,
    ResolutionError,
    AgentRegistry,
    AgentStatus,
)


class TestResolutionRecorder:
    """Unit tests for the ResolutionRecorder."""

    def test_record_first_resolution(self):
        recorder = ResolutionRecorder()
        recorder.record("task-1", "handler-a", "agent-1")
        assert recorder.is_pinned("task-1")
        assert recorder.get_pinned_handler("task-1") == "handler-a"

    def test_record_duplicate_same_handler_succeeds(self):
        """Recording the same handler again should be idempotent."""
        recorder = ResolutionRecorder()
        recorder.record("task-1", "handler-a", "agent-1")
        # Same handler, same task — should not raise
        recorder.record("task-1", "handler-a", "agent-1")
        assert recorder.is_pinned("task-1")

    def test_record_mismatched_handler_raises(self):
        """Recording a different handler for a pinned task must raise."""
        recorder = ResolutionRecorder()
        recorder.record("task-1", "handler-a", "agent-1")
        with pytest.raises(ResolutionError, match="already pinned"):
            recorder.record("task-1", "handler-b", "agent-1")

    def test_not_pinned_returns_false(self):
        recorder = ResolutionRecorder()
        assert not recorder.is_pinned("nonexistent")
        assert recorder.get_pinned_handler("nonexistent") is None

    def test_validate_matching_handler(self):
        recorder = ResolutionRecorder()
        recorder.record("task-1", "handler-a", "agent-1")
        assert recorder.validate("task-1", "handler-a") is True

    def test_validate_mismatched_handler(self):
        recorder = ResolutionRecorder()
        recorder.record("task-1", "handler-a", "agent-1")
        assert recorder.validate("task-1", "handler-b") is False

    def test_validate_unpinned_task(self):
        recorder = ResolutionRecorder()
        assert recorder.validate("task-1", "handler-a") is False

    def test_invalidate_for_agent(self):
        recorder = ResolutionRecorder()
        recorder.record("task-1", "handler-a", "agent-1")
        recorder.record("task-2", "handler-b", "agent-1")
        recorder.record("task-3", "handler-c", "agent-2")

        count = recorder.invalidate_for_agent("agent-1")
        assert count == 2
        assert not recorder.is_pinned("task-1")
        assert not recorder.is_pinned("task-2")
        assert recorder.is_pinned("task-3")  # agent-2 untouched

    def test_invalidate_nonexistent_agent(self):
        recorder = ResolutionRecorder()
        recorder.record("task-1", "handler-a", "agent-1")
        count = recorder.invalidate_for_agent("agent-nonexistent")
        assert count == 0
        assert recorder.is_pinned("task-1")

    def test_multiple_tasks_same_agent(self):
        """Multiple tasks pinned to the same agent — all invalidated."""
        recorder = ResolutionRecorder()
        for i in range(5):
            recorder.record(f"task-{i}", f"handler-{i % 2}", "agent-shared")
        count = recorder.invalidate_for_agent("agent-shared")
        assert count == 5
        assert not any(recorder.is_pinned(f"task-{i}") for i in range(5))


class TestRegistryInvalidation:
    """Integration tests: AgentRegistry invalidates recorder on lifecycle changes."""

    def test_delete_agent_invalidates_resolutions(self):
        registry = AgentRegistry()
        agent_id = registry.register("worker", "builder.default")
        recorder = registry.recorder
        recorder.record("task-1", "handler-a", agent_id)
        assert recorder.is_pinned("task-1")

        registry.delete(agent_id)
        assert not recorder.is_pinned("task-1")

    def test_stop_agent_invalidates_resolutions(self):
        registry = AgentRegistry()
        agent_id = registry.register("worker", "builder.default")
        recorder = registry.recorder
        recorder.record("task-1", "handler-a", agent_id)

        registry.update_status(agent_id, AgentStatus.STOPPED)
        assert not recorder.is_pinned("task-1")

    def test_fail_agent_invalidates_resolutions(self):
        registry = AgentRegistry()
        agent_id = registry.register("worker", "builder.default")
        recorder = registry.recorder
        recorder.record("task-1", "handler-a", agent_id)

        registry.update_status(agent_id, AgentStatus.FAILED)
        assert not recorder.is_pinned("task-1")

    def test_terminate_agent_invalidates_resolutions(self):
        registry = AgentRegistry()
        agent_id = registry.register("worker", "builder.default")
        recorder = registry.recorder
        recorder.record("task-1", "handler-a", agent_id)

        registry.update_status(agent_id, AgentStatus.TERMINATED)
        assert not recorder.is_pinned("task-1")

    def test_pause_agent_does_not_invalidate(self):
        """Pausing keeps the agent available — resolutions stay valid."""
        registry = AgentRegistry()
        agent_id = registry.register("worker", "builder.default")
        recorder = registry.recorder
        recorder.record("task-1", "handler-a", agent_id)

        registry.update_status(agent_id, AgentStatus.PAUSED)
        assert recorder.is_pinned("task-1")

    def test_run_agent_does_not_invalidate(self):
        registry = AgentRegistry()
        agent_id = registry.register("worker", "builder.default")
        recorder = registry.recorder
        recorder.record("task-1", "handler-a", agent_id)

        registry.update_status(agent_id, AgentStatus.RUNNING)
        assert recorder.is_pinned("task-1")

    def test_invalidation_only_affects_target_agent(self):
        """Invalidating one agent must not affect resolutions of others."""
        registry = AgentRegistry()
        agent_1 = registry.register("worker-1", "builder.default")
        agent_2 = registry.register("worker-2", "builder.default")
        recorder = registry.recorder
        recorder.record("task-1", "handler-a", agent_1)
        recorder.record("task-2", "handler-b", agent_2)

        registry.delete(agent_1)
        assert not recorder.is_pinned("task-1")
        assert recorder.is_pinned("task-2")  # agent-2 untouched

    def test_mid_run_stale_resolution_rejected(self):
        """Task resolved to handler that was then invalidated — must be caught."""
        registry = AgentRegistry()
        agent_id = registry.register("worker", "builder.default")
        recorder = registry.recorder
        recorder.record("task-1", "handler-a", agent_id)

        # Agent stops mid-run
        registry.update_status(agent_id, AgentStatus.STOPPED)
        assert not recorder.is_pinned("task-1")

        # New resolution attempt for same task must still be accepted
        # (since old pin was invalidated)
        recorder.record("task-1", "handler-b", agent_id)
        assert recorder.get_pinned_handler("task-1") == "handler-b"
