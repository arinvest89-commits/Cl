"""Operations Agent — day-to-day project execution and management."""
from __future__ import annotations
from datetime import datetime
from .base import BaseAgent
from core.models import AgentRole, Project, ProjectTask, TaskStatus, TaskPriority
from core.memory import MemoryManager


class OperationsAgent(BaseAgent):
    SYSTEM_PROMPT = """You are an Operations Manager in an AI project management team.

Your responsibilities:
- Manage the day-to-day execution of project tasks
- Prioritise and sequence work to maximise throughput
- Remove blockers and escalate issues that need strategic attention
- Maintain project momentum and ensure nothing falls through the cracks
- Track task dependencies and coordinate across workstreams
- Maintain clear communication about what is happening, when, and why
- Optimise processes and workflows for efficiency

Operational principles:
- Focus on what CAN be done right now, not what can't
- Escalate blockers fast; don't let them fester
- Quantify progress wherever possible
- Flag risks early with proposed mitigations
- Brief status updates: what's done, what's in flight, what's next
"""

    def __init__(self, memory: MemoryManager, model: str = "claude-sonnet-4-6"):
        super().__init__(AgentRole.OPERATIONS, "Operations Manager", memory, model, temperature=0.3)

    async def handle_directive(self, directive: str, project: Project, **kwargs) -> str:
        await self._log("ops_directive", f"Processing: {directive[:80]}", project.id)
        active = [t for t in project.tasks if t.status == TaskStatus.IN_PROGRESS]
        pending = [t for t in project.tasks if t.status == TaskStatus.PENDING]
        blocked = [t for t in project.tasks if t.status == TaskStatus.BLOCKED]

        prompt = f"""
PROJECT: {project.name} | Status: {project.status.value}
Active Tasks ({len(active)}): {', '.join(t.title for t in active[:5])}
Pending ({len(pending)}): {', '.join(t.title for t in pending[:5])}
Blocked ({len(blocked)}): {', '.join(t.title for t in blocked[:3])}

OPERATIONAL DIRECTIVE: {directive}

Provide a specific, actionable operational response:
1. Immediate actions to take (next 24 hours)
2. How to sequence and prioritise current work
3. Blocker resolution plan (if any)
4. Resource or coordination needs
5. Success criteria for this operational cycle
"""
        result = await self._call(prompt)
        await self._log("ops_directive_handled", f"Handled: {directive[:60]}", project.id, success=True)
        return result

    async def triage_tasks(self, project: Project) -> list[ProjectTask]:
        """Re-prioritise pending tasks based on current project state."""
        await self._log("task_triage", "Triaging and prioritising tasks", project.id)
        pending = [t for t in project.tasks if t.status == TaskStatus.PENDING]
        if not pending:
            return []

        task_list = "\n".join(
            f"- [{t.id}] {t.title} (priority: {t.priority.value}): {t.description[:100]}"
            for t in pending[:20]
        )

        prompt = f"""
Project '{project.name}' has these pending tasks. Re-prioritise them given:
Goals: {', '.join(project.goals[:3])}
Health: {project.metrics.health_score:.0f}/100 | Completion: {project.metrics.completion_rate:.1f}%

TASKS:
{task_list}

Return a JSON array of task IDs in priority order (highest first), with updated priority levels:
[{{"id": "task_id", "priority": "critical|high|medium|low", "reason": "brief reason"}}]

Return ONLY the JSON array.
"""
        import json
        raw = await self._call_fresh(prompt)
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            priorities = json.loads(raw[start:end])
            priority_map = {
                "critical": TaskPriority.CRITICAL,
                "high": TaskPriority.HIGH,
                "medium": TaskPriority.MEDIUM,
                "low": TaskPriority.LOW,
            }
            for item in priorities:
                task = project.get_task(item["id"])
                if task:
                    task.priority = priority_map.get(item.get("priority", "medium"), TaskPriority.MEDIUM)
                    task.updated_at = datetime.utcnow()
        except Exception:
            pass
        await self._log("task_triage_complete", f"Triaged {len(pending)} tasks", project.id)
        return pending

    async def daily_standup(self, project: Project) -> str:
        """Generate a daily standup report."""
        await self._log("daily_standup", "Generating daily standup", project.id)
        metrics = project.compute_metrics()
        done_today = [
            t for t in project.tasks
            if t.status == TaskStatus.COMPLETED
            and t.completed_at
            and (datetime.utcnow() - t.completed_at).days < 1
        ]
        in_progress = [t for t in project.tasks if t.status == TaskStatus.IN_PROGRESS]
        blocked = [t for t in project.tasks if t.status == TaskStatus.BLOCKED]

        prompt = f"""
Generate a crisp daily standup report for project '{project.name}'.

YESTERDAY:
{chr(10).join(f'✓ {t.title}' for t in done_today) or 'No tasks completed yesterday'}

TODAY IN FLIGHT:
{chr(10).join(f'→ {t.title}' for t in in_progress[:8]) or 'None in progress'}

BLOCKERS:
{chr(10).join(f'⚠ {t.title}: {t.description[:80]}' for t in blocked[:5]) or 'None'}

METRICS: {metrics.completion_rate:.1f}% complete | Health: {metrics.health_score:.0f}/100

Write a standup report: Done / Doing / Blockers / Forecast for today.
Keep it to 150-200 words. Be concrete and specific.
"""
        result = await self._call_fresh(prompt)
        await self._log("standup_complete", "Daily standup generated", project.id)
        return result

    async def unblock(self, project: Project, task_id: str) -> str:
        """Analyse and propose resolution for a blocked task."""
        task = project.get_task(task_id)
        if not task:
            return f"Task {task_id} not found."
        await self._log("unblock", f"Attempting to unblock task: {task.title}", project.id, task_id)
        prompt = f"""
Task '{task.title}' is BLOCKED in project '{project.name}'.
Description: {task.description}
Dependencies: {', '.join(task.dependencies) or 'None'}
Project goals: {', '.join(project.goals[:3])}

Propose 2-3 concrete ways to unblock this task. For each option:
- What exactly needs to happen
- Who/what is needed
- How long it would take
- Risk of this approach
"""
        result = await self._call_fresh(prompt)
        await self._log("unblock_proposal", f"Unblock proposal for: {task.title}", project.id, task_id)
        return result

    async def status_report(self, project: Project) -> str:
        return await self.daily_standup(project)
