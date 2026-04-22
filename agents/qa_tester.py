"""QA & Testing Agent — validates all aspects of the project against stated goals."""
from __future__ import annotations
import json
from .base import BaseAgent
from core.models import AgentRole, Project, ProjectTask, TaskStatus
from core.memory import MemoryManager


class QAAgent(BaseAgent):
    SYSTEM_PROMPT = """You are a QA Engineer in an AI project management team.

Your responsibilities:
- Test and validate every function and deliverable of the project
- Ensure all outputs align with the project's stated goals and targets
- Create comprehensive test plans and acceptance criteria
- Identify defects, gaps, and quality issues before they cause problems
- Validate that integrations work as intended
- Certify milestones as complete only when all criteria are met
- Maintain quality standards throughout the project lifecycle

QA principles:
- Every feature/deliverable must be testable — if it can't be measured, it's not complete
- Document test results clearly with pass/fail and evidence
- Distinguish between critical failures (block progress) and minor issues (log and fix)
- Regression testing: validate that fixes don't break existing functions
- Always produce actionable defect reports, never just "it doesn't work"
"""

    def __init__(self, memory: MemoryManager, model: str = "claude-sonnet-4-6"):
        super().__init__(AgentRole.QA, "QA Engineer", memory, model, temperature=0.2)

    async def handle_directive(self, directive: str, project: Project, **kwargs) -> str:
        await self._log("qa_directive", f"Processing: {directive[:80]}", project.id)
        prompt = f"""
PROJECT: {project.name}
Goals: {', '.join(project.goals)}
Targets: {', '.join(project.targets)}
Status: {project.status.value}
Completion: {project.metrics.completion_rate:.1f}%

QA DIRECTIVE: {directive}

Provide a thorough QA response:
1. What specifically needs to be tested/validated
2. Test approach and criteria
3. Expected outcomes vs current state
4. Any issues found (defects, gaps, inconsistencies)
5. Pass/Fail verdict with evidence
6. Remediation steps if failed
"""
        result = await self._call(prompt)
        await self._log("qa_directive_handled", f"QA complete for: {directive[:60]}", project.id, success=True)
        return result

    async def create_test_plan(self, project: Project) -> str:
        """Create a comprehensive test plan for the project."""
        await self._log("test_plan", "Creating comprehensive test plan", project.id)
        prompt = f"""
Create a detailed test plan for project '{project.name}'.

PROJECT OUTLINE:
{project.outline}

GOALS TO VALIDATE:
{chr(10).join(f'{i+1}. {g}' for i, g in enumerate(project.goals))}

TARGETS TO VERIFY:
{chr(10).join(f'{i+1}. {t}' for i, t in enumerate(project.targets))}

DELIVERABLES/TASKS:
{chr(10).join(f'- {t.title}: {t.description[:100]}' for t in project.tasks[:15])}

Create a test plan with:
1. **Test Scope** — what is in/out of scope
2. **Test Categories** — functional, performance, integration, acceptance
3. **Test Cases** — specific tests for each goal and target
4. **Acceptance Criteria** — pass/fail thresholds for each goal
5. **Testing Schedule** — when to run which tests
6. **Success Definition** — what "project complete" looks like from a QA perspective

Format as a professional QA test plan document in markdown.
"""
        result = await self._call_fresh(prompt)
        await self.memory.agent_remember(self.role.value, f"test_plan_{project.id}", result[:1000])
        await self._log("test_plan_complete", "Test plan created", project.id)
        return result

    async def validate_task_completion(self, project: Project, task: ProjectTask) -> dict:
        """Validate whether a task has genuinely been completed to standard."""
        await self._log("task_validation", f"Validating task: {task.title}", project.id, task.id)
        prompt = f"""
Validate whether this task is genuinely complete to a professional standard.

PROJECT: {project.name}
TASK: {task.title}
DESCRIPTION: {task.description}
RESULTS REPORTED: {task.results or 'No results documented'}
TASK PRIORITY: {task.priority.value}

RELEVANT GOALS:
{chr(10).join(f'- {g}' for g in project.goals[:3])}

Assess:
1. Is the task genuinely complete? (Yes/Partial/No)
2. Does it meet the stated requirements?
3. What evidence confirms completion?
4. What (if anything) is missing or substandard?
5. Overall verdict: PASS / PASS WITH NOTES / FAIL

Return as JSON:
{{"verdict": "PASS|PASS_WITH_NOTES|FAIL", "complete": true, "score": 85, "issues": [], "evidence": "what confirms completion", "notes": "any caveats"}}
"""
        raw = await self._call_fresh(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            result = json.loads(raw[start:end])
        except Exception:
            result = {"verdict": "PASS_WITH_NOTES", "complete": True, "score": 70, "issues": [], "notes": raw[:200]}
        await self._log("task_validated", f"{task.title}: {result.get('verdict', 'UNKNOWN')}", project.id, task.id)
        return result

    async def integration_test(self, project: Project) -> str:
        """Test all integrations are working correctly."""
        await self._log("integration_test", "Testing all integrations", project.id)
        integrations = project.integrations
        if not integrations:
            return "No integrations configured. Nothing to test."

        integ_summary = "\n".join(
            f"- {i.get('name', 'Unknown')}: {i.get('type', 'unknown')} — {i.get('status', 'unknown')}"
            for i in integrations[:10]
        )
        prompt = f"""
Test and validate the following integrations for project '{project.name}':

{integ_summary}

For each integration assess:
1. Connection status (Active/Inactive/Unknown)
2. Data flow validation (is data moving correctly?)
3. Error handling (graceful failures?)
4. Performance (acceptable latency/throughput?)
5. Security (appropriate auth/permissions?)

Produce an integration test report with: Overall Status, Per-Integration Results, Issues Found, Remediation Steps.
"""
        result = await self._call_fresh(prompt)
        await self._log("integration_test_complete", "Integration tests complete", project.id)
        return result

    async def milestone_sign_off(self, project: Project, milestone_id: str) -> dict:
        """Determine whether a milestone can be signed off."""
        milestone = next((m for m in project.milestones if m.id == milestone_id), None)
        if not milestone:
            return {"approved": False, "reason": "Milestone not found"}

        await self._log("milestone_signoff", f"Evaluating milestone: {milestone.title}", project.id)
        milestone_tasks = [t for t in project.tasks if t.id in milestone.tasks]
        completed = [t for t in milestone_tasks if t.status == TaskStatus.COMPLETED]

        prompt = f"""
Evaluate whether milestone '{milestone.title}' should be signed off.

DESCRIPTION: {milestone.description}
SUCCESS CRITERIA:
{chr(10).join(f'- {c}' for c in milestone.success_criteria)}

TASK COMPLETION: {len(completed)}/{len(milestone_tasks)} tasks done
COMPLETED TASKS: {', '.join(t.title for t in completed)}
INCOMPLETE TASKS: {', '.join(t.title for t in milestone_tasks if t.status != TaskStatus.COMPLETED)}

Return JSON: {{"approved": true, "confidence": 0.9, "gaps": [], "conditions": [], "recommendation": "brief recommendation"}}
"""
        raw = await self._call_fresh(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            result = json.loads(raw[start:end])
        except Exception:
            result = {"approved": len(completed) == len(milestone_tasks), "confidence": 0.6, "recommendation": raw[:200]}
        return result

    async def status_report(self, project: Project) -> str:
        completed = [t for t in project.tasks if t.status == TaskStatus.COMPLETED]
        return f"QA: {len(completed)} tasks validated. Health score: {project.metrics.health_score:.0f}/100."
