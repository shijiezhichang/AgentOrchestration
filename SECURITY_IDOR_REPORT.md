"""
IDOR Vulnerability Report: Unauthorized Cross-Agent Task Assignment
===================================================================

Severity: CRITICAL ($5,000 - $10,000)
Component: src/orchestrator/engine.py — OrchestrationEngine._execute_task

VULNERABILITY
-------------
The OrchestrationEngine._execute_task() method reads task["target_agent"]
and dispatches execution to that agent without ANY authorization check:

    agent_id = task["target_agent"]   # ← NO PERMISSION CHECK

There is no validation that:
1. The caller has permission to run tasks on the target agent
2. The target agent exists and is in a runnable state
3. The task belongs to the same workspace/tenant as the agent

IMPACT
------
- Cross-tenant task execution: User A can run arbitrary code on User B's agent
- Agent state bypass: Tasks execute on STOPPED/FAILED/TERMINATED agents
- Privilege escalation: Low-privilege user executes tasks on admin agents
- Resource hijacking: Attacker consumes victim's agent compute resources

PROOF OF CONCEPT (PoC)
----------------------
Run: python tests/test_idor_poc.py

This test demonstrates:
1. User-A's agent is registered
2. User-B enqueues a task targeting User-A's agent (without permission)
3. The engine executes it — bypassing ownership entirely

FIX
---
Add task ownership and agent state validation in _execute_task:
1. Require task["owner"] field matching the authenticated caller
2. Validate target agent exists and is in {RUNNING, PENDING, PAUSED}
3. Reject execution on terminal-state agents
4. Validate workspace/tenant isolation

See fix in: src/orchestrator/engine.py (lines 45-50)
"""
