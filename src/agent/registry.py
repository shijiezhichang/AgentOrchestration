"""Agent Registry — Manages agent lifecycle and metadata."""

import json
import logging
import re
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Allow alphanumeric, hyphens, underscores, dots. No path traversal chars.
_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_MAX_NAME_LENGTH = 128


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    TERMINATED = "terminated"


class RegistryError(ValueError):
    """Raised when a registry operation is rejected due to policy violation."""


def _validate_agent_name(name: str) -> None:
    """Validate agent name against path traversal and injection risks.

    Rejects names that contain:
    - Path traversal sequences (..)
    - Directory separators (/ or \\)
    - Null bytes
    - Leading/trailing whitespace
    - Characters outside [a-zA-Z0-9_.-]
    - Empty or excessively long names
    """
    if not name or not name.strip():
        raise RegistryError("Agent name must not be empty")
    if len(name) > _MAX_NAME_LENGTH:
        raise RegistryError(
            f"Agent name exceeds {_MAX_NAME_LENGTH} characters"
        )
    if "\x00" in name:
        raise RegistryError("Agent name contains null byte")
    if not _NAME_PATTERN.match(name):
        raise RegistryError(
            f"Agent name '{name}' contains invalid characters. "
            "Only alphanumeric, hyphens, underscores, and dots are allowed. "
            "Must start with a letter or digit."
        )


class AgentRegistry:
    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, List[str]] = {}

    def register(
        self, name: str, agent_type: str, config: Optional[Dict] = None
    ) -> str:
        # Validate name against path traversal before any state mutation
        try:
            _validate_agent_name(name)
        except RegistryError as e:
            logger.warning(
                "Rejected agent registration: name=%r reason=%s", name, e
            )
            raise

        agent_id = str(uuid.uuid4())
        timestamp = time.time()
        self._agents[agent_id] = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "status": AgentStatus.PENDING.value,
            "config": config or {},
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": "1.0.0",
            "metrics": {"tasks_completed": 0, "errors": 0, "uptime": 0},
        }
        group = agent_type.split(".")[0]
        if group not in self._index:
            self._index[group] = []
        self._index[group].append(agent_id)
        logger.info(
            "Agent registered: id=%s name=%s type=%s",
            agent_id, name, agent_type,
        )
        return agent_id

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._agents.get(agent_id)

    def list(
        self, status: Optional[AgentStatus] = None, group: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        agents = self._agents.values()
        if status:
            agents = [a for a in agents if a["status"] == status.value]
        if group:
            agent_ids = self._index.get(group, [])
            agents = [a for a in agents if a["id"] in agent_ids]
        return list(agents)

    def update_status(self, agent_id: str, status: AgentStatus) -> bool:
        if agent_id not in self._agents:
            return False
        self._agents[agent_id]["status"] = status.value
        self._agents[agent_id]["updated_at"] = time.time()
        return True

    def delete(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)
        return True

    def count(self) -> int:
        return len(self._agents)
