import pytest
from src.agent.registry import (
    AgentRegistry,
    AgentStatus,
    RegistryError,
    _validate_agent_name,
)


class TestAgentRegistry:
    def setup_method(self):
        self.registry = AgentRegistry()

    def test_register_agent(self):
        agent_id = self.registry.register("test-agent", "worker.processor")
        assert agent_id is not None
        assert self.registry.count() == 1

    def test_get_agent(self):
        agent_id = self.registry.register("test-agent", "worker.processor")
        agent = self.registry.get(agent_id)
        assert agent is not None
        assert agent["name"] == "test-agent"
        assert agent["type"] == "worker.processor"

    def test_get_nonexistent_agent(self):
        agent = self.registry.get("nonexistent-id")
        assert agent is None

    def test_list_agents(self):
        self.registry.register("agent-1", "worker.processor")
        self.registry.register("agent-2", "worker.analyzer")
        self.registry.register("agent-3", "monitor.watcher")
        assert len(self.registry.list()) == 3

    def test_list_agents_by_group(self):
        self.registry.register("agent-1", "worker.processor")
        self.registry.register("agent-2", "monitor.watcher")
        workers = self.registry.list(group="worker")
        assert len(workers) == 1

    def test_update_status(self):
        agent_id = self.registry.register("test-agent", "worker.processor")
        assert self.registry.update_status(agent_id, AgentStatus.RUNNING)
        agent = self.registry.get(agent_id)
        assert agent["status"] == "running"

    def test_delete_agent(self):
        agent_id = self.registry.register("test-agent", "worker.processor")
        assert self.registry.delete(agent_id)
        assert self.registry.count() == 0

    def test_delete_nonexistent_agent(self):
        assert not self.registry.delete("nonexistent-id")


class TestAgentNameValidation:
    """Regression tests for handler name path traversal prevention (Issue #19)."""

    # Valid names
    @pytest.mark.parametrize("name", [
        "my-agent",
        "worker_1",
        "agent.v2",
        "task-runner-prod",
        "a",
        "A" * 128,
        "123agent",
        "UPPER_CASE",
    ])
    def test_valid_names_are_accepted(self, name):
        _validate_agent_name(name)  # should not raise

    # Path traversal attacks
    @pytest.mark.parametrize("name,description", [
        ("../etc/passwd", "parent directory traversal"),
        ("..\\windows\\system32", "Windows path traversal"),
        ("agent/../secret", "embedded path traversal"),
        ("/etc/shadow", "absolute path"),
        ("C:\\Windows", "Windows absolute path"),
        (".", "current directory dot"),
        ("..", "parent directory dots"),
        ("agent\x00hidden", "null byte injection"),
        (" agent ", "leading/trailing whitespace"),
        ("", "empty string"),
        ("   ", "whitespace only"),
        ("a" * 129, "exceeds max length"),
        ("agent;rm -rf /", "command injection attempt"),
        ("agent|malicious", "pipe injection attempt"),
    ])
    def test_path_traversal_names_are_rejected(self, name, description):
        with pytest.raises(RegistryError, match="invalid|empty|exceeds|null"):
            _validate_agent_name(name)

    def test_registry_register_rejects_traversal(self):
        registry = AgentRegistry()
        with pytest.raises(RegistryError):
            registry.register("../malicious", "worker.processor")

    def test_registry_register_rejects_null_byte(self):
        registry = AgentRegistry()
        with pytest.raises(RegistryError):
            registry.register("good\x00evil", "worker.processor")

    def test_registry_register_rejects_empty(self):
        registry = AgentRegistry()
        with pytest.raises(RegistryError):
            registry.register("", "worker.processor")

    def test_valid_registration_still_works(self):
        registry = AgentRegistry()
        agent_id = registry.register("valid-agent-1", "worker.processor")
        assert agent_id is not None
        assert registry.count() == 1

    def test_registry_count_unchanged_after_rejection(self):
        registry = AgentRegistry()
        registry.register("good-agent", "worker.processor")
        assert registry.count() == 1
        with pytest.raises(RegistryError):
            registry.register("../bad", "worker.processor")
        assert registry.count() == 1  # not incremented
