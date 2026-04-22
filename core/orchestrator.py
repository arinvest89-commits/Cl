"""
Orchestrator — the Project Manager agent that coordinates the entire agent team.
All directives, decisions, and communications flow through here.
"""
from __future__ import annotations
import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

import anthropic
import os

from .models import (
    Project, ProjectTask, Milestone, ProjectStatus, TaskStatus, TaskPriority,
    ApprovalRequest, ApprovalStatus, AgentRole, ChatMessage, MessageRole,
    AgentActivity, ACPMessage,
)
from .memory import MemoryManager
from agents.researcher import ResearchAgent
from agents.strategist import StrategistAgent
from agents.operations import OperationsAgent
from agents.forecaster import ForecastingAgent
from agents.qa_tester import QAAgent
from agents.integrator import IntegrationAgent


class Orchestrator:
    """
    Central Project Manager that owns the full project lifecycle.
    Delegates work to specialist agents, manages approvals,
    and maintains the single source of truth for project state.
    """

    SYSTEM_PROMPT = """You are the Project Manager AI — the lead agent coordinating a full specialist team.

Your team:
- Research Analyst: market research, competitive analysis, performance data
- Strategy Lead: strategies, plans, task creation
- Operations Manager: day-to-day execution, task management, blockers
- Forecasting Analyst: timelines, risks, resource needs
- QA Engineer: testing, validation, quality assurance
- Integration Specialist: tools, APIs, external resources

Your role:
- Synthesise input from all agents into coherent decisions
- Route tasks to the right specialist
- Maintain focus on project goals and targets at all times
- Identify when user approval is needed and structure clear decision requests
- Communicate clearly, concisely, and with authority
- Never lose sight of the project's end state

Communication style:
- Direct and confident, not hedge-y
- Structured: what you know, what you're doing, what you need
- Escalate to the user only for material decisions, not routine operations
- Always tie recommendations back to project goals
"""

    def __init__(self, memory: MemoryManager, model: str = "claude-sonnet-4-6"):
        self.memory = memory
        self.model = model
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self._conversation: list[dict[str, Any]] = []

        # Specialist agents
        self.researcher = ResearchAgent(memory, model)
        self.strategist = StrategistAgent(memory, model)
        self.operations = OperationsAgent(memory, model)
        self.forecaster = ForecastingAgent(memory, model)
        self.qa = QAAgent(memory, model)
        self.integrator = IntegrationAgent(memory, model)

        self._agents = {
            AgentRole.RESEARCHER: self.researcher,
            AgentRole.STRATEGIST: self.strategist,
            AgentRole.OPERATIONS: self.operations,
            AgentRole.FORECASTER: self.forecaster,
            AgentRole.QA: self.qa,
            AgentRole.INTEGRATOR: self.integrator,
        }

        # Callbacks for UI updates
        self._message_callbacks: list[Callable] = []
        self._approval_callbacks: list[Callable] = []
        self._activity_callbacks: list[Callable] = []

        # Register activity callbacks on all agents
        for agent in self._agents.values():
            agent.on_activity(self._relay_activity)

        # Background task handles
        self._background_tasks: list[asyncio.Task] = []
        self._running = False

    # ── Callback registration ────────────────────────────────────────────────

    def on_message(self, cb: Callable) -> None:
        self._message_callbacks.append(cb)

    def on_approval_needed(self, cb: Callable) -> None:
        self._approval_callbacks.append(cb)

    def on_activity(self, cb: Callable) -> None:
        self._activity_callbacks.append(cb)

    def _relay_activity(self, activity: AgentActivity) -> None:
        for cb in self._activity_callbacks:
            try:
                cb(activity)
            except Exception:
                pass

    async def _emit_message(self, content: str, agent: Optional[AgentRole] = None, project_id: Optional[str] = None) -> None:
        msg = ChatMessage(
            role=MessageRole.AGENT,
            agent=agent or AgentRole.ORCHESTRATOR,
            content=content,
            project_id=project_id,
        )
        await self.memory.save_chat_message(msg)
        for cb in self._message_callbacks:
            try:
                cb(msg)
            except Exception:
                pass

    async def _emit_approval(self, approval: ApprovalRequest) -> None:
        await self.memory.save_approval(approval)
        for cb in self._approval_callbacks:
            try:
                cb(approval)
            except Exception:
                pass

    # ── LLM calls ────────────────────────────────────────────────────────────

    async def _call(self, prompt: str) -> str:
        self._conversation.append({"role": "user", "content": prompt})
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=self.SYSTEM_PROMPT,
                    messages=list(self._conversation),
                ),
            )
            text = response.content[0].text
            self._conversation.append({"role": "assistant", "content": text})
            if len(self._conversation) > 50:
                self._conversation = self._conversation[-50:]
            return text
        except Exception as e:
            return f"Orchestrator error: {e}"

    async def _call_fresh(self, prompt: str) -> str:
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=self.SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )
            return response.content[0].text
        except Exception as e:
            return f"Error: {e}"

    # ── Project lifecycle ─────────────────────────────────────────────────────

    async def initialise_project(self, name: str, outline: str, goals: list[str], targets: list[str]) -> Project:
        """Create a new project and kick off full initialisation sequence."""
        project = Project(name=name, outline=outline, goals=goals, targets=targets)
        await self.memory.save_project(project)

        await self._emit_message(
            f"Project '{name}' created. Initiating strategy development and task breakdown...",
            project_id=project.id,
        )

        # Step 1: Initial strategy
        strategy = await self.strategist.create_initial_strategy(project)
        project.strategies.append({
            "id": str(uuid.uuid4())[:8],
            "title": "Initial Strategy",
            "summary": strategy.get("strategy_summary", ""),
            "pillars": strategy.get("strategic_pillars", []),
            "created_at": datetime.utcnow().isoformat(),
        })

        # Step 2: Create tasks from strategy
        role_map = {
            "researcher": AgentRole.RESEARCHER,
            "strategist": AgentRole.STRATEGIST,
            "operations": AgentRole.OPERATIONS,
            "forecaster": AgentRole.FORECASTER,
            "qa": AgentRole.QA,
            "integrator": AgentRole.INTEGRATOR,
        }
        priority_map = {
            "critical": TaskPriority.CRITICAL,
            "high": TaskPriority.HIGH,
            "medium": TaskPriority.MEDIUM,
            "low": TaskPriority.LOW,
        }
        for task_def in strategy.get("tasks", [])[:20]:
            project.tasks.append(ProjectTask(
                title=task_def.get("title", "Task"),
                description=task_def.get("description", ""),
                priority=priority_map.get(task_def.get("priority", "medium"), TaskPriority.MEDIUM),
                assigned_to=role_map.get(task_def.get("assigned_to", "operations"), AgentRole.OPERATIONS),
            ))

        # Step 3: Create initial milestones from phases
        for i, phase in enumerate(strategy.get("phases", [])[:5]):
            project.milestones.append(Milestone(
                title=phase.get("name", f"Phase {i+1}"),
                description=phase.get("objective", ""),
            ))

        # Step 4: Discover tools
        tools = await self.integrator.discover_tools(project)
        high_value = [t for t in tools if t.get("estimated_impact") == "high" and t.get("priority") == "immediate"]
        for tool in high_value[:3]:
            plan = await self.integrator.create_integration_plan(project, tool)
            project.integrations.append(plan)

        # Step 5: Initial forecast
        project.status = ProjectStatus.ACTIVE
        project.compute_metrics()
        await self.forecaster.generate_forecast(project)

        await self.memory.save_project(project)

        # Step 6: Request approval for strategy
        approval = ApprovalRequest(
            project_id=project.id,
            requesting_agent=AgentRole.ORCHESTRATOR,
            decision_type="strategy_approval",
            title=f"Approve Initial Strategy for '{name}'",
            description=(
                f"Strategy summary: {strategy.get('strategy_summary', 'N/A')}\n\n"
                f"Pillars: {', '.join(strategy.get('strategic_pillars', []))}\n\n"
                f"Tasks created: {len(project.tasks)}\n"
                f"Tools queued: {len(project.integrations)}\n"
                f"Quick wins: {', '.join(strategy.get('quick_wins', []))}"
            ),
            options=["Approve and proceed", "Approve with modifications", "Reject and revise"],
            recommended_option="Approve and proceed",
            impact_level="high",
        )
        await self._emit_approval(approval)
        await self._emit_message(
            f"Project initialised successfully.\n"
            f"• {len(project.tasks)} tasks created\n"
            f"• {len(project.milestones)} milestones defined\n"
            f"• {len(project.integrations)} integrations queued\n"
            f"• Strategy approval request raised — please review.",
            project_id=project.id,
        )
        return project

    async def run_daily_cycle(self, project_id: str) -> None:
        """Run the daily management cycle for a project."""
        project = await self.memory.load_project(project_id)
        if not project or project.status != ProjectStatus.ACTIVE:
            return

        await self._emit_message("Running daily management cycle...", project_id=project_id)

        # 1. Operations standup
        standup = await self.operations.daily_standup(project)
        await self._emit_message(f"**Daily Standup**\n{standup}", AgentRole.OPERATIONS, project_id)

        # 2. Research market scan (every 3rd cycle placeholder — always run here)
        research = await self.researcher.analyse_project_performance(project)

        # 3. Generate new tasks from research
        new_tasks = await self.strategist.generate_tasks_from_research(project, research)
        for task in new_tasks:
            project.tasks.append(task)

        # 4. Triage and prioritise
        await self.operations.triage_tasks(project)

        # 5. Refresh forecast
        await self.forecaster.generate_forecast(project)

        # 6. Integration health check
        health = await self.integrator.integration_health_check(project)
        if health.get("issues"):
            await self._emit_message(
                f"Integration health issues detected: {health['issues']}",
                AgentRole.INTEGRATOR, project_id,
            )

        # 7. Compute metrics and save
        project.compute_metrics()
        await self.memory.save_project(project)

        await self._emit_message(
            f"Daily cycle complete. {len(new_tasks)} new tasks added. "
            f"Completion: {project.metrics.completion_rate:.1f}% | Health: {project.metrics.health_score:.0f}/100",
            project_id=project_id,
        )

    # ── User communication ────────────────────────────────────────────────────

    async def chat(self, user_message: str, project_id: Optional[str] = None) -> str:
        """Handle a message from the user."""
        user_msg = ChatMessage(
            role=MessageRole.USER,
            content=user_message,
            project_id=project_id,
        )
        await self.memory.save_chat_message(user_msg)

        project = None
        project_context = ""
        if project_id:
            project = await self.memory.load_project(project_id)
            if project:
                project.compute_metrics()
                project_context = f"""
ACTIVE PROJECT: {project.name}
Status: {project.status.value} | Completion: {project.metrics.completion_rate:.1f}% | Health: {project.metrics.health_score:.0f}/100
Goals: {', '.join(project.goals[:3])}
Targets: {', '.join(project.targets[:3])}
Active tasks: {project.metrics.tasks_in_progress} | Pending: {project.metrics.tasks_total - project.metrics.tasks_completed - project.metrics.tasks_in_progress}
"""

        pending_approvals = await self.memory.load_approvals(pending_only=True)
        approval_context = (
            f"\nPENDING APPROVALS: {len(pending_approvals)} awaiting your decision.\n"
            if pending_approvals else ""
        )

        prompt = f"""{project_context}{approval_context}
USER MESSAGE: {user_message}

Respond as the Project Manager. If this is a question, answer it with full context.
If it's a directive, acknowledge it, explain what you'll do, and immediately delegate to the right agents.
If it touches a pending approval, reference it.
Be direct and concrete.
"""
        response = await self._call(prompt)

        # Check if we need to trigger agent actions based on the conversation
        if project and any(kw in user_message.lower() for kw in
                          ["research", "analyse", "analyze", "strategy", "forecast", "test", "integrate", "status"]):
            asyncio.create_task(self._handle_action_request(user_message, project))

        await self._emit_message(response, AgentRole.ORCHESTRATOR, project_id)
        return response

    async def _handle_action_request(self, message: str, project: Project) -> None:
        """Delegate specific action requests to the right agent."""
        msg = message.lower()
        try:
            if "research" in msg or "market" in msg or "analyse" in msg or "analyze" in msg:
                result = await self.researcher.handle_directive(message, project)
                await self._emit_message(result, AgentRole.RESEARCHER, project.id)

            if "strategy" in msg or "plan" in msg:
                result = await self.strategist.handle_directive(message, project)
                await self._emit_message(result, AgentRole.STRATEGIST, project.id)

            if "forecast" in msg or "predict" in msg or "timeline" in msg:
                await self.forecaster.generate_forecast(project)
                result = await self.forecaster.handle_directive(message, project)
                await self._emit_message(result, AgentRole.FORECASTER, project.id)

            if "test" in msg or "qa" in msg or "quality" in msg or "validate" in msg:
                result = await self.qa.handle_directive(message, project)
                await self._emit_message(result, AgentRole.QA, project.id)

            if "integrat" in msg or "tool" in msg or "resource" in msg:
                result = await self.integrator.handle_directive(message, project)
                await self._emit_message(result, AgentRole.INTEGRATOR, project.id)

            if "status" in msg or "standup" in msg or "update" in msg:
                result = await self.operations.daily_standup(project)
                await self._emit_message(result, AgentRole.OPERATIONS, project.id)

            project.compute_metrics()
            await self.memory.save_project(project)
        except Exception as e:
            await self._emit_message(f"Error handling request: {e}", project_id=project.id)

    # ── Approval resolution ───────────────────────────────────────────────────

    async def resolve_approval(self, approval_id: str, choice: str, notes: str = "") -> str:
        """Process the user's decision on an approval request."""
        approvals = await self.memory.load_approvals()
        approval = next((a for a in approvals if a.id == approval_id), None)
        if not approval:
            return f"Approval {approval_id} not found."

        approval.status = ApprovalStatus.APPROVED if "approve" in choice.lower() or choice == approval.options[0] else ApprovalStatus.REJECTED
        approval.chosen_option = choice
        approval.resolution_notes = notes
        approval.resolved_at = datetime.utcnow()
        await self.memory.save_approval(approval)

        project = await self.memory.load_project(approval.project_id)
        if project:
            response = await self._call(
                f"The user has resolved approval request '{approval.title}' "
                f"with decision: '{choice}'. Notes: {notes or 'none'}. "
                f"What actions should the team take next as a result of this decision?"
            )
            await self._emit_message(response, project_id=approval.project_id)

            if approval.status == ApprovalStatus.APPROVED:
                asyncio.create_task(self._execute_post_approval(approval, project, choice))

        return f"Approval resolved: {choice}"

    async def _execute_post_approval(self, approval: ApprovalRequest, project: Project, choice: str) -> None:
        """Execute follow-up actions after an approval is granted."""
        try:
            if approval.decision_type == "strategy_approval":
                # Activate first tasks
                pending = [t for t in project.tasks if t.status == TaskStatus.PENDING]
                for task in pending[:3]:
                    task.status = TaskStatus.IN_PROGRESS
                    task.updated_at = datetime.utcnow()
                await self.memory.save_project(project)
                await self._emit_message(
                    f"Strategy approved. Activating first {min(3, len(pending))} tasks.",
                    project_id=project.id,
                )

            elif approval.decision_type == "integration_approval":
                result = await self.integrator.implement_integration(
                    project, {"name": approval.metadata.get("tool_name", "Unknown")}
                )
                await self._emit_message(result, AgentRole.INTEGRATOR, project.id)

            elif approval.decision_type == "milestone_completion":
                milestone_id = approval.metadata.get("milestone_id")
                if milestone_id:
                    milestone = next((m for m in project.milestones if m.id == milestone_id), None)
                    if milestone:
                        milestone.status = TaskStatus.COMPLETED
                        await self.memory.save_project(project)
        except Exception as e:
            await self._emit_message(f"Post-approval execution error: {e}", project_id=project.id)

    # ── Team status ───────────────────────────────────────────────────────────

    async def full_team_status(self, project: Project) -> dict[str, str]:
        """Collect status reports from all agents."""
        tasks = [
            (AgentRole.RESEARCHER, self.researcher.status_report(project)),
            (AgentRole.STRATEGIST, self.strategist.status_report(project)),
            (AgentRole.OPERATIONS, self.operations.status_report(project)),
            (AgentRole.FORECASTER, self.forecaster.status_report(project)),
            (AgentRole.QA, self.qa.status_report(project)),
            (AgentRole.INTEGRATOR, self.integrator.status_report(project)),
        ]
        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
        return {
            tasks[i][0].value: str(results[i]) if not isinstance(results[i], Exception) else f"Error: {results[i]}"
            for i in range(len(tasks))
        }

    async def synthesise_status(self, project: Project) -> str:
        """Generate an executive summary of the project."""
        team_status = await self.full_team_status(project)
        forecast_info = ""
        if project.forecast:
            days = (project.forecast.predicted_completion - datetime.utcnow()).days if project.forecast.predicted_completion else "?"
            forecast_info = f"Forecast: ~{days} days to completion (confidence: {project.forecast.confidence:.0%})"

        prompt = f"""
Synthesise this team status into an executive project summary for the user.

PROJECT: {project.name}
Status: {project.status.value} | {project.metrics.completion_rate:.1f}% complete | Health: {project.metrics.health_score:.0f}/100
{forecast_info}

TEAM REPORTS:
{chr(10).join(f'{role.upper()}: {status}' for role, status in team_status.items())}

Write a 150-200 word executive summary: key progress, risks, next priorities, overall health.
"""
        return await self._call_fresh(prompt)

    # ── ACP integration ────────────────────────────────────────────────────────

    async def handle_acp_message(self, message: ACPMessage) -> Optional[ACPMessage]:
        """Handle an incoming ACP message from an external agent."""
        project_id = message.payload.get("project_id")
        project = await self.memory.load_project(project_id) if project_id else None

        response_payload: dict[str, Any] = {"status": "received"}

        if message.action == "status_request":
            if project:
                response_payload["status_summary"] = await self.synthesise_status(project)
            else:
                response_payload["error"] = "Project not found"

        elif message.action == "task_handoff":
            task_data = message.payload.get("task", {})
            if project and task_data:
                task = ProjectTask(
                    title=task_data.get("title", "External Task"),
                    description=task_data.get("description", ""),
                    metadata={"source_agent": message.sender},
                )
                project.tasks.append(task)
                await self.memory.save_project(project)
                response_payload["task_id"] = task.id

        elif message.action == "data_share":
            if project:
                result = await self.researcher.handle_directive(
                    f"Analyse this data from {message.sender}: {message.payload.get('data', '')[:1000]}",
                    project,
                )
                response_payload["analysis"] = result

        elif message.action == "broadcast":
            await self._emit_message(
                f"[ACP from {message.sender}]: {message.payload.get('message', '')}",
                project_id=project_id,
            )

        if message.requires_response:
            return ACPMessage(
                sender="orchestrator",
                recipient=message.sender,
                message_type="response",
                action=f"{message.action}_response",
                payload=response_payload,
                correlation_id=message.id,
            )
        return None
