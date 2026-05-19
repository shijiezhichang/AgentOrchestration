import pytest
from src.agent.registry import AgentRegistry, AgentStatus, HandlerPinnedError


class TestHandlerPinning:
    """Regression tests for handler pinning per attempt (Issue #8)."""

    def setup_method(self):
        self.registry = AgentRegistry()

    def test_pin_handler_creates_snapshot(self):
        agent_id = self.registry.register("worker-1", "worker.processor")
        pin = self.registry.pin_handler(agent_id, "task-001")
        assert pin["agent_id"] == agent_id
        assert pin["task_id"] == "task-001"
        assert pin["snapshot"]["name"] == "worker-1"
        assert pin["valid"] is True
        assert "pinned_at" in pin

    def test_pin_handler_nonexistent_agent(self):
        with pytest.raises(HandlerPinnedError, match="not found"):
            self.registry.pin_handler("nonexistent", "task-001")

    def test_resolve_pinned_returns_valid_snapshot(self):
        agent_id = self.registry.register("worker-1", "worker.processor")
        self.registry.pin_handler(agent_id, "task-001")
        pin = self.registry.resolve_pinned("task-001")
        assert pin["snapshot"]["name"] == "worker-1"
        assert pin["valid"] is True

    def test_resolve_unpinned_task_fails(self):
        with pytest.raises(HandlerPinnedError, match="No pinned handler"):
            self.registry.resolve_pinned("never-pinned")

    def test_pin_invalidated_on_agent_deletion(self):
        agent_id = self.registry.register("worker-1", "worker.processor")
        self.registry.pin_handler(agent_id, "task-001")

        # Delete the agent — pin should be invalidated
        self.registry.delete(agent_id)

        with pytest.raises(HandlerPinnedError, match="deleted"):
            self.registry.resolve_pinned("task-001")

    def test_pin_invalidated_on_status_update(self):
        agent_id = self.registry.register("worker-1", "worker.processor")
        pin = self.registry.pin_handler(agent_id, "task-001")
        assert pin["valid"] is True

        # Update status — pin should be invalidated
        self.registry.update_status(agent_id, AgentStatus.FAILED)

        with pytest.raises(HandlerPinnedError, match="no longer valid"):
            self.registry.resolve_pinned("task-001")

    def test_pin_snapshot_is_deep_copy(self):
        """Verify the snapshot is independent of the live agent."""
        agent_id = self.registry.register("worker-1", "worker.processor")
        pin = self.registry.pin_handler(agent_id, "task-001")

        # Modify the live agent
        agent = self.registry.get(agent_id)
        agent["name"] = "modified-name"

        # Snapshot should still have original name
        assert pin["snapshot"]["name"] == "worker-1"

    def test_multiple_pins_independent(self):
        agent_id = self.registry.register("worker-1", "worker.processor")

        self.registry.pin_handler(agent_id, "task-001")
        self.registry.pin_handler(agent_id, "task-002")

        # Delete agent — both pins invalidated
        self.registry.delete(agent_id)

        for tid in ["task-001", "task-002"]:
            with pytest.raises(HandlerPinnedError):
                self.registry.resolve_pinned(tid)

    def test_pin_preserves_expected_lifecycle_state(self):
        """Agent count and index are preserved after pinning."""
        agent_id = self.registry.register("worker-1", "worker.processor")
        assert self.registry.count() == 1

        self.registry.pin_handler(agent_id, "task-001")
        assert self.registry.count() == 1  # count unchanged

    def test_pin_does_not_prevent_valid_registration(self):
        """Pinning one agent doesn't block registering new ones."""
        agent_id = self.registry.register("worker-1", "worker.processor")
        self.registry.pin_handler(agent_id, "task-001")

        new_id = self.registry.register("worker-2", "worker.processor")
        assert new_id is not None
        assert self.registry.count() == 2
