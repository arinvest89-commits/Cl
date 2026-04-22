"""Strategy Agent — creates, refines and implements project strategies and plans."""
from __future__ import annotations
import json
from datetime import datetime
from .base import BaseAgent
from core.models import AgentRole, Project, ProjectTask, TaskPriority, TaskStatus
from core.memory import MemoryManager


class StrategistAgent(BaseAgent):
    SYSTEM_PROMPT = """You are a senior Strategy Lead in an AI project management team.

Your responsibilities:
- Develop comprehensive strategies and detailed implementation plans
- Break strategies into concrete, actionable tasks with clear owners and timelines
- Identify and mitigate strategic risks before they materialise
- Adapt strategies based on new data, market changes, and performance results
- Ensure all plans are tightly aligned with project goals and targets
- Think both short-term (immediate tactics) and long-term (strategic positioning)

When producing plans or strategies:
- Always output structured JSON for tasks so they can be directly imported
- Be specific: vague strategies fail; concrete actions succeed
- Include success criteria for every major initiative
- Flag dependencies and prerequisites explicitly
- Consider resource constraints realistically
"""

    def __init__(self, memory: MemoryManager, model: str = "claude-sonnet-4-6"):
        super().__init__(AgentRole.STRATEGIST, "Strategy Lead", memory, model, temperature=0.5)

    async def handle_directive(self, directive: str, project: Project, **kwargs) -> str:
        await self._log("strategy_directive", f"Processing: {directive[:80]}", project.id)
        prompt = f"""
PROJECT CONTEXT:
Name: {project.name}
Outline: {project.outline}
Goals: {chr(10).join(f'- {g}' for g in project.goals)}
Targets: {chr(10).join(f'- {t}' for t in project.targets)}
Current Status: {project.status.value}
Existing Strategies: {len(project.strategies)}
Active Tasks: {sum(1 for t in project.tasks if t.status == TaskStatus.IN_PROGRESS)}

DIRECTIVE: {directive}

Develop a strategic response. Include:
1. Strategic assessment and context
2. Recommended approach with clear rationale
3. Implementation steps (as a numbered plan)
4. Success metrics and KPIs
5. Risk mitigation
6. Timeline estimate
"""
        result = await self._call(prompt)
        await self._log("strategy_delivered", f"Strategy for: {directive[:60]}", project.id, success=True)
        return result

    async def create_initial_strategy(self, project: Project) -> dict:
        """Generate the full initial strategy and task breakdown for a new project."""
        await self._log("initial_strategy", "Creating initial strategy and task breakdown", project.id)
        prompt = f"""
Create a comprehensive project strategy for:

PROJECT: {project.name}
OUTLINE: {project.outline}

GOALS:
{chr(10).join(f'{i+1}. {g}' for i, g in enumerate(project.goals))}

TARGETS:
{chr(10).join(f'{i+1}. {t}' for i, t in enumerate(project.targets))}

TODAY'S DATE: {datetime.utcnow().strftime('%Y-%m-%d')}

Produce a response in this EXACT JSON structure:
{{
  "strategy_summary": "2-3 sentence overview",
  "strategic_pillars": ["pillar1", "pillar2", "pillar3"],
  "phases": [
    {{
      "name": "Phase name",
      "duration_days": 14,
      "objective": "Phase objective",
      "key_activities": ["activity1", "activity2"]
    }}
  ],
  "tasks": [
    {{
      "title": "Task title",
      "description": "Detailed description",
      "priority": "high|medium|low|critical",
      "assigned_to": "researcher|strategist|operations|forecaster|qa|integrator",
      "estimated_days": 3
    }}
  ],
  "success_metrics": ["metric1", "metric2"],
  "key_risks": ["risk1", "risk2"],
  "quick_wins": ["win1", "win2"]
}}

Return ONLY the JSON object, no other text.
"""
        raw = await self._call_fresh(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            strategy = json.loads(raw[start:end])
        except Exception:
            strategy = {
                "strategy_summary": raw[:300],
                "strategic_pillars": [],
                "phases": [],
                "tasks": [],
                "success_metrics": [],
                "key_risks": [],
                "quick_wins": [],
            }

        await self.memory.agent_remember(
            self.role.value, f"strategy_{project.id}", strategy
        )
        await self._log("initial_strategy_complete", f"Strategy created with {len(strategy.get('tasks', []))} tasks", project.id)
        return strategy

    async def generate_tasks_from_research(self, project: Project, research: str) -> list[ProjectTask]:
        """Convert research findings into actionable tasks."""
        await self._log("task_generation", "Generating tasks from research", project.id)
        prompt = f"""
Based on this research for project '{project.name}':

{research[:2000]}

Generate 3-5 specific, actionable tasks to capitalise on the findings.
Return ONLY JSON array:
[
  {{
    "title": "Specific action title",
    "description": "What to do and why, based on the research",
    "priority": "high|medium|low",
    "assigned_to": "researcher|strategist|operations|forecaster|qa|integrator"
  }}
]
"""
        raw = await self._call_fresh(prompt)
        tasks = []
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            task_defs = json.loads(raw[start:end])
            for td in task_defs:
                priority_map = {
                    "critical": TaskPriority.CRITICAL,
                    "high": TaskPriority.HIGH,
                    "medium": TaskPriority.MEDIUM,
                    "low": TaskPriority.LOW,
                }
                tasks.append(ProjectTask(
                    title=td.get("title", "New Task"),
                    description=td.get("description", ""),
                    priority=priority_map.get(td.get("priority", "medium"), TaskPriority.MEDIUM),
                ))
        except Exception:
            pass
        await self._log("task_generation_complete", f"Generated {len(tasks)} tasks from research", project.id)
        return tasks

    async def adapt_strategy(self, project: Project, trigger: str) -> str:
        """Revise strategy in response to new information or performance data."""
        await self._log("strategy_adaptation", f"Adapting strategy: {trigger[:60]}", project.id)
        strategies_summary = "\n".join(
            f"- {s.get('title', 'Strategy')}: {s.get('summary', '')}"
            for s in project.strategies[:5]
        )
        prompt = f"""
The project '{project.name}' requires strategy adaptation.

TRIGGER: {trigger}

CURRENT STRATEGIES:
{strategies_summary or 'None yet defined'}

PROJECT HEALTH: {project.metrics.health_score:.0f}/100
COMPLETION: {project.metrics.completion_rate:.1f}%

Provide:
1. Assessment of what changed and why it matters
2. Specific strategy adjustments required
3. Tasks to add, modify, or remove
4. Updated priorities
5. Revised success metrics if needed
"""
        result = await self._call(prompt)
        await self._log("strategy_adapted", "Strategy adaptation complete", project.id)
        return result

    async def status_report(self, project: Project) -> str:
        strategy_count = len(project.strategies)
        return f"Strategy Lead: {strategy_count} active strategies. Project at {project.metrics.completion_rate:.0f}% completion."
