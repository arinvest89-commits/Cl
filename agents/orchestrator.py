"""
OrchestratorAgent — the Project Manager.

Responsibilities:
  - Understands and owns the project vision, goals and targets
  - Breaks work into tasks and delegates to specialist agents
  - Coordinates the autonomous review cycle
  - Routes user messages to the right agent or handles them directly
  - Surfaces key decisions to the user for approval
  - Synthesises outputs from all agents into a coherent project narrative
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from agents.base import BaseAgent
from config import AUTONOMOUS_LOOP_INTERVAL
from core.models import AgentType, ChatMessage, TaskStatus
from core.state import state


ORCHESTRATOR_SYSTEM = """You are the Project Manager of an autonomous agent team.

Your team includes:
  • Strategy Agent    — creates and maintains strategic plans
  • Research Agent    — conducts market research and data analysis
  • Implementation Agent — executes plans, integrates tools and resources
  • Forecasting Agent — predicts future needs and performance
  • QA Agent         — tests and validates every aspect of the project

YOUR ROLE:
1. Own the project goals and targets at all times.
2. Break work into clear tasks and create them with the `create_task` tool.
3. Run autonomous review cycles: analyse progress, identify gaps, reassign work.
4. Use `request_approval` for any key decision before acting on it.
5. Communicate clearly with the user — provide regular progress updates via `send_message`.
6. Co-ordinate external agents (openclaw, paperclip) via `communicate_with_external_agent`.
7. Keep `get_project_state` up to date and refer to it before making decisions.

DECISION FRAMEWORK — always seek approval when:
  - Committing to a new strategic direction
  - Integrating a paid tool or service
  - Changing key project targets
  - Any irreversible action

COMMUNICATION STYLE:
  - Be direct and concise
  - Lead with what matters: status, blockers, decisions
  - Use markdown for structure
  - Surface risks proactively
"""


class OrchestratorAgent(BaseAgent):
    AGENT_TYPE = AgentType.ORCHESTRATOR
    SYSTEM_PROMPT = ORCHESTRATOR_SYSTEM

    def __init__(self) -> None:
        super().__init__()
        self._sub_agents: dict[AgentType, BaseAgent] = {}
        self._autonomous_task: asyncio.Task | None = None
        self._conversation_history: list[dict] = []
        self._running = False

    # ─── Sub-agent registry ──────────────────────────────────────────────────

    def register_agent(self, agent: BaseAgent) -> None:
        self._sub_agents[agent.AGENT_TYPE] = agent

    # ─── Project initialisation ──────────────────────────────────────────────

    async def initialise_project(self, project_info: dict) -> str:
        """Called once when a new project is handed to the team."""
        prompt = f"""A new project has just been registered. Analyse it thoroughly and:

1. Confirm your understanding of the goals and targets
2. Create an initial set of tasks for each specialist agent to begin with
3. Outline your high-level execution strategy
4. Identify any immediate risks or dependencies
5. Send a welcome message to the user summarising the plan

Project details:
```json
{json.dumps(project_info, indent=2)}
```
"""
        response = await self.run(prompt)
        return response

    # ─── User message routing ─────────────────────────────────────────────────

    async def handle_user_message(self, message: str) -> str:
        """Process a user message in the context of the ongoing project."""
        # Add to rolling conversation history (keep last 20 turns)
        self._conversation_history.append({"role": "user", "content": message})
        if len(self._conversation_history) > 40:
            self._conversation_history = self._conversation_history[-40:]

        prompt = f"""The user has sent you a message. Respond helpfully, take any necessary
actions (create tasks, request approvals, send updates), and ensure the project stays on track.

User message: {message}
"""
        response = await self.run(prompt, history=self._conversation_history[:-1])

        # Store the assistant reply in history
        self._conversation_history.append({"role": "assistant", "content": response})

        # Save to persistent message store
        await state.add_message(
            ChatMessage(sender="agent_team", content=response, message_type="chat")
        )
        return response

    # ─── Autonomous loop ──────────────────────────────────────────────────────

    def start_autonomous_loop(self) -> None:
        if self._autonomous_task and not self._autonomous_task.done():
            return
        self._running = True
        self._autonomous_task = asyncio.create_task(self._autonomous_loop())

    def stop_autonomous_loop(self) -> None:
        self._running = False
        if self._autonomous_task:
            self._autonomous_task.cancel()

    async def _autonomous_loop(self) -> None:
        """Periodically review the project and dispatch work without user input."""
        while self._running:
            try:
                await asyncio.sleep(AUTONOMOUS_LOOP_INTERVAL)
                project = await state.get_active_project()
                if project is None:
                    continue

                await self._run_autonomous_cycle(project)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                await state.log(
                    "orchestrator",
                    "autonomous_loop_error",
                    str(exc),
                    level="error",
                )

    async def _run_autonomous_cycle(self, project: Any) -> None:
        """One autonomous review cycle."""
        tasks = await state.get_tasks(project.id)
        strategies = await state.get_strategies(project.id)
        forecasts = await state.get_forecasts(project.id)
        pending_approvals = await state.get_pending_approvals()

        summary = {
            "project": project.model_dump(),
            "task_summary": {
                "total": len(tasks),
                "done": sum(1 for t in tasks if t.status == "done"),
                "running": sum(1 for t in tasks if t.status == "running"),
                "pending": sum(1 for t in tasks if t.status == "pending"),
                "failed": sum(1 for t in tasks if t.status == "failed"),
            },
            "active_strategies": len(strategies),
            "recent_forecasts": len(forecasts),
            "pending_approvals": len(pending_approvals),
            "timestamp": datetime.utcnow().isoformat(),
        }

        await self.run(
            f"""Autonomous review cycle — analyse project health and take action.

Current snapshot:
```json
{json.dumps(summary, indent=2)}
```

Tasks:
1. Review task completion and create new tasks if any area needs attention
2. Check if research / strategy / implementation / forecasting / QA agents need new work
3. Identify any risks or blockers and escalate or mitigate
4. If any key decision is needed, use request_approval
5. Send a brief progress update to the user via send_message
"""
        )

    # ─── Delegate to sub-agent ────────────────────────────────────────────────

    async def delegate(self, agent_type: AgentType, task: str, context: dict | None = None) -> str:
        agent = self._sub_agents.get(agent_type)
        if agent is None:
            return f"Agent {agent_type.value} not registered"
        result = await agent.run(task, context=context)
        return result
