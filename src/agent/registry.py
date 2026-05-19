"""Agent Registry — Manages agent lifecycle and metadata."""

import copy
import json
import logging
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    TERMINATED = "terminated"


class HandlerPinnedError(RuntimeError):
    """Raised when a pinned handler is no longer valid."""


class AgentRegistry:
    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, List[str]] = {}
        self._pins: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, agent_type: str, config: Optional[Dict] = None) -> str:
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
        return agent_id

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._agents.get(agent_id)

    def list(self, status: Optional[AgentStatus] = None, group: Optional[str] = None) -> List[Dict[str, Any]]:
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
        # Invalidate any pins for this agent — handler is no longer reliable
        self._invalidate_pin(agent_id)
        return True

    def delete(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)
        self._invalidate_pin(agent_id)
        return True

    def count(self) -> int:
        return len(self._agents)

    # ---- Handler Pinning (Issue #8) ----

    def pin_handler(self, agent_id: str, task_id: str) -> Dict[str, Any]:
        """Create an immutable snapshot of the handler at resolution time.
        
        Returns a deep copy of the agent state pinned to this task_id.
        Logs the pin event for audit trail.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            raise HandlerPinnedError(
                f"Cannot pin handler: agent {agent_id} not found"
            )
        pinned = {
            "agent_id": agent_id,
            "task_id": task_id,
            "snapshot": copy.deepcopy(agent),
            "pinned_at": time.time(),
            "valid": True,
        }
        self._pins[task_id] = pinned
        logger.info(
            "Handler pinned: agent=%s task=%s type=%s",
            agent_id, task_id, agent["type"],
        )
        return pinned

    def resolve_pinned(self, task_id: str) -> Dict[str, Any]:
        """Resolve a pinned handler and verify it is still valid.

        Checks:
        - Pin exists
        - Pin is still marked valid
        - Agent still exists in registry
        - Agent ID matches the pinned snapshot

        Returns the pinned snapshot if valid.
        Raises HandlerPinnedError if the resolution is stale or invalid.
        """
        pin = self._pins.get(task_id)
        if not pin:
            raise HandlerPinnedError(
                f"No pinned handler for task {task_id}"
            )
        if not pin["valid"]:
            # Determine the specific reason
            agent_id = pin["agent_id"]
            if agent_id not in self._agents:
                raise HandlerPinnedError(
                    f"Agent {agent_id} was deleted after handler was pinned "
                    f"for task {task_id}"
                )
            raise HandlerPinnedError(
                f"Pinned handler for task {task_id} is no longer valid — "
                "agent was modified mid-run"
            )
        agent_id = pin["agent_id"]
        current = self._agents.get(agent_id)
        if not current:
            raise HandlerPinnedError(
                f"Agent {agent_id} was deleted after handler was pinned "
                f"for task {task_id}"
            )
        if current["id"] != pin["snapshot"]["id"]:
            raise HandlerPinnedError(
                f"Agent {agent_id} identity mismatch for task {task_id}"
            )
        logger.debug(
            "Pinned handler resolved: agent=%s task=%s", agent_id, task_id
        )
        return pin

    def _invalidate_pin(self, agent_id: str) -> None:
        """Mark all pins for this agent as invalid."""
        for task_id, pin in list(self._pins.items()):
            if pin["agent_id"] == agent_id and pin["valid"]:
                pin["valid"] = False
                logger.warning(
                    "Handler pin invalidated: agent=%s task=%s", agent_id, task_id
                )
