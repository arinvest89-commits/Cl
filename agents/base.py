"""Base agent class — all specialist agents inherit from this."""
from __future__ import annotations
import asyncio
import os
from datetime import datetime
from typing import Any, AsyncIterator, Optional
import anthropic
from core.models import AgentActivity, AgentRole, Project, ProjectTask, TaskStatus
from core.memory import MemoryManager


class BaseAgent:
    """Foundation for all agents in the team."""

    SYSTEM_PROMPT: str = "You are a helpful AI agent."

    def __init__(
        self,
        role: AgentRole,
        name: str,
        memory: MemoryManager,
        model: str = "claude-sonnet-4-6",
        temperature: float = 0.3,
    ):
        self.role = role
        self.name = name
        self.memory = memory
        self.model = model
        self.temperature = temperature
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self._conversation: list[dict[str, Any]] = []
        self._active = True
        self._activity_callbacks: list = []

    # ── Internal helpers ────────────────────────────────────────────────────

    def _build_messages(self, user_content: str) -> list[dict[str, Any]]:
        self._conversation.append({"role": "user", "content": user_content})
        return list(self._conversation)

    def _record_assistant(self, content: str) -> None:
        self._conversation.append({"role": "assistant", "content": content})
        if len(self._conversation) > 40:
            self._conversation = self._conversation[-40:]

    def reset_conversation(self) -> None:
        self._conversation = []

    # ── Core LLM call ────────────────────────────────────────────────────────

    async def _call(self, prompt: str, system_override: Optional[str] = None) -> str:
        system = system_override or self.SYSTEM_PROMPT
        messages = self._build_messages(prompt)
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=system,
                    messages=messages,
                ),
            )
            text = response.content[0].text
            self._record_assistant(text)
            return text
        except Exception as e:
            error = f"[{self.name}] LLM call failed: {e}"
            self._record_assistant(f"Error: {e}")
            return error

    async def _call_fresh(self, prompt: str, system_override: Optional[str] = None) -> str:
        """Single-turn call without conversation history."""
        system = system_override or self.SYSTEM_PROMPT
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )
            return response.content[0].text
        except Exception as e:
            return f"Error: {e}"

    # ── Activity logging ─────────────────────────────────────────────────────

    async def _log(
        self,
        action: str,
        description: str,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        success: bool = True,
    ) -> None:
        activity = AgentActivity(
            agent=self.role,
            action=action,
            description=description,
            project_id=project_id,
            task_id=task_id,
            timestamp=datetime.utcnow(),
            success=success,
        )
        await self.memory.log_activity(activity)
        for cb in self._activity_callbacks:
            try:
                cb(activity)
            except Exception:
                pass

    def on_activity(self, callback) -> None:
        self._activity_callbacks.append(callback)

    # ── Task helpers ────────────────────────────────────────────────────────

    async def _complete_task(self, project: Project, task_id: str, result: str) -> None:
        task = project.get_task(task_id)
        if task:
            task.status = TaskStatus.COMPLETED
            task.results = result
            task.completed_at = datetime.utcnow()
            task.updated_at = datetime.utcnow()

    async def _fail_task(self, project: Project, task_id: str, reason: str) -> None:
        task = project.get_task(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.results = reason
            task.updated_at = datetime.utcnow()

    # ── Abstract interface ───────────────────────────────────────────────────

    async def handle_directive(self, directive: str, project: Project, **kwargs) -> str:
        """Process a directive from the orchestrator."""
        raise NotImplementedError

    async def status_report(self, project: Project) -> str:
        """Return a brief status report for this agent's domain."""
        raise NotImplementedError
