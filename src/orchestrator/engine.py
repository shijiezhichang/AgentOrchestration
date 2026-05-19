"""Orchestration Engine — Core execution and coordination logic."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from src.agent import AgentRegistry, AgentStatus
from src.agent.registry import HandlerPinnedError
from src.orchestrator.scheduler import TaskScheduler

logger = logging.getLogger(__name__)


class OrchestrationEngine:
    def __init__(self, max_workers: int = 10, agent_timeout: int = 300):
        self.registry = AgentRegistry()
        self.scheduler = TaskScheduler()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.agent_timeout = agent_timeout
        self._running = False
        self._hooks: Dict[str, List[Callable]] = {
            "pre_execute": [],
            "post_execute": [],
            "on_error": [],
            "on_complete": [],
        }

    def register_hook(self, event: str, callback: Callable) -> None:
        if event in self._hooks:
            self._hooks[event].append(callback)

    def enqueue_task(self, task: Dict, queue: str = "default", priority: int = 0) -> str:
        """Enqueue a task and pin the handler resolution.

        The handler (agent) is pinned at enqueue time so that mid-run
        registry updates (deletion, status change) do not affect the
        task's resolved handler.
        """
        agent_id = task.get("target_agent")
        if not agent_id:
            raise ValueError("Task must specify target_agent")

        task_id = self.scheduler.enqueue(task, queue, priority)
        # Pin the handler — this creates an immutable snapshot
        self.registry.pin_handler(agent_id, task_id)
        logger.info(
            "Task %s enqueued with handler %s pinned", task_id, agent_id
        )
        return task_id

    async def start(self) -> None:
        self._running = True
        logger.info("Orchestration engine started")
        while self._running:
            task = await self.scheduler.dequeue()
            if task:
                asyncio.create_task(self._execute_task(task))
            await asyncio.sleep(0.1)

    def stop(self) -> None:
        self._running = False
        logger.info("Orchestration engine stopped")

    async def _execute_task(self, task: Dict[str, Any]) -> None:
        task_id = task["id"]

        # Resolve the pinned handler — validates it is still current
        try:
            pin = self.registry.resolve_pinned(task_id)
            agent_id = pin["agent_id"]
        except HandlerPinnedError as e:
            logger.error("Task %s aborted: %s", task_id, e)
            for hook in self._hooks["on_error"]:
                await hook(task, e)
            return

        logger.info("Executing task %s on agent %s", task_id, agent_id)

        for hook in self._hooks["pre_execute"]:
            await hook(task)

        try:
            agent = pin["snapshot"]
            self.registry.update_status(agent_id, AgentStatus.RUNNING)
            result = await asyncio.wait_for(
                self._run_agent_task(agent, task),
                timeout=self.agent_timeout,
            )
            self.registry.update_status(agent_id, AgentStatus.PAUSED)

            for hook in self._hooks["post_execute"]:
                await hook(task, result)

            logger.info("Task %s completed successfully", task_id)

        except Exception as e:
            logger.error("Task %s failed: %s", task_id, e)
            for hook in self._hooks["on_error"]:
                await hook(task, e)

    async def _run_agent_task(self, agent: Dict, task: Dict) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            self._execute_in_thread,
            agent,
            task,
        )

    def _execute_in_thread(self, agent: Dict, task: Dict) -> Any:
        return {"status": "completed", "output": f"Task {task['id']} processed by {agent['name']}"}
