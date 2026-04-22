"""Forecasting Agent — predicts future needs, risks and project trajectory."""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from .base import BaseAgent
from core.models import AgentRole, Project, ForecastData, TaskStatus
from core.memory import MemoryManager


class ForecastingAgent(BaseAgent):
    SYSTEM_PROMPT = """You are a Forecasting Analyst in an AI project management team.

Your responsibilities:
- Predict project completion timelines with confidence intervals
- Forecast resource needs (tools, expertise, capacity) 30/60/90 days out
- Model risk scenarios and their probability/impact
- Identify early warning signals before they become problems
- Provide data-driven recommendations to keep the project on track
- Continuously refine forecasts as new data arrives

Forecasting principles:
- Be honest about uncertainty; always state confidence levels
- Base predictions on current velocity and observable trends
- Distinguish between signal and noise in the data
- Scenarios: best case / base case / worst case
- Always recommend pre-emptive actions to improve the forecast
"""

    def __init__(self, memory: MemoryManager, model: str = "claude-sonnet-4-6"):
        super().__init__(AgentRole.FORECASTER, "Forecasting Analyst", memory, model, temperature=0.2)

    async def handle_directive(self, directive: str, project: Project, **kwargs) -> str:
        await self._log("forecast_directive", f"Processing: {directive[:80]}", project.id)
        metrics = project.compute_metrics()
        prompt = f"""
PROJECT: {project.name} | Status: {project.status.value}
Completion: {metrics.completion_rate:.1f}% | Health: {metrics.health_score:.0f}/100
Tasks: {metrics.tasks_total} total, {metrics.tasks_completed} done, {metrics.tasks_in_progress} active, {metrics.tasks_blocked} blocked
Goals: {', '.join(project.goals)}
Targets: {', '.join(project.targets)}

FORECASTING DIRECTIVE: {directive}

Provide a thorough forecast analysis covering:
1. Current trajectory assessment
2. Predicted outcomes (best/base/worst case)
3. Key risk factors with probabilities
4. Resource and capacity needs
5. Recommended pre-emptive actions
6. Early warning indicators to watch
"""
        result = await self._call(prompt)
        await self._log("forecast_delivered", f"Forecast for: {directive[:60]}", project.id, success=True)
        return result

    async def generate_forecast(self, project: Project, horizon_days: int = 30) -> ForecastData:
        """Generate a structured forecast for the project."""
        await self._log("generate_forecast", f"Generating {horizon_days}-day forecast", project.id)
        metrics = project.compute_metrics()
        velocity = metrics.velocity if metrics.velocity > 0 else 1.0
        remaining = metrics.tasks_total - metrics.tasks_completed
        est_days = remaining / velocity if velocity > 0 else 999

        prompt = f"""
Generate a detailed project forecast for '{project.name}'.

CURRENT STATE:
- Completion: {metrics.completion_rate:.1f}%
- Tasks remaining: {remaining}
- Estimated velocity: {velocity:.1f} tasks/day
- Blocked tasks: {metrics.tasks_blocked}
- Health score: {metrics.health_score:.0f}/100
- Forecast horizon: {horizon_days} days
- Raw ETA estimate: {est_days:.0f} days

GOALS: {', '.join(project.goals)}
TARGETS: {', '.join(project.targets)}

Return ONLY a JSON object with this structure:
{{
  "predicted_completion_days": 45,
  "confidence": 0.72,
  "risks": [
    {{"title": "Risk name", "probability": 0.3, "impact": "high", "mitigation": "action"}}
  ],
  "opportunities": [
    {{"title": "Opportunity name", "potential": "description", "action_required": "what to do"}}
  ],
  "resource_needs": [
    {{"resource": "what is needed", "when_days": 14, "reason": "why", "priority": "high|medium|low"}}
  ],
  "recommendations": [
    "Specific recommendation 1",
    "Specific recommendation 2"
  ],
  "scenarios": {{
    "best_case_days": 30,
    "base_case_days": 45,
    "worst_case_days": 75
  }}
}}
"""
        raw = await self._call_fresh(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            data = json.loads(raw[start:end])
            completion_days = data.get("predicted_completion_days", est_days)
            predicted = datetime.utcnow() + timedelta(days=completion_days)
            forecast = ForecastData(
                horizon_days=horizon_days,
                predicted_completion=predicted,
                confidence=data.get("confidence", 0.5),
                risks=data.get("risks", []),
                opportunities=data.get("opportunities", []),
                resource_needs=data.get("resource_needs", []),
                recommendations=data.get("recommendations", []),
            )
        except Exception:
            forecast = ForecastData(
                horizon_days=horizon_days,
                predicted_completion=datetime.utcnow() + timedelta(days=est_days),
                confidence=0.4,
                recommendations=["Insufficient data for detailed forecast; increase task velocity."],
            )
        project.forecast = forecast
        await self.memory.agent_remember(
            self.role.value,
            f"forecast_{project.id}",
            {"generated": datetime.utcnow().isoformat(), "confidence": forecast.confidence},
        )
        await self._log("forecast_complete", f"Forecast generated — confidence {forecast.confidence:.0%}", project.id)
        return forecast

    async def risk_assessment(self, project: Project) -> str:
        """Deep-dive risk assessment."""
        await self._log("risk_assessment", "Running risk assessment", project.id)
        blocked = [t for t in project.tasks if t.status == TaskStatus.BLOCKED]
        prompt = f"""
Conduct a comprehensive risk assessment for project '{project.name}'.

CONTEXT:
{project.outline[:500]}

CURRENT ISSUES:
- Blocked tasks ({len(blocked)}): {', '.join(t.title for t in blocked[:5])}
- Health score: {project.metrics.health_score:.0f}/100

For each identified risk provide:
- Risk description
- Probability (Low/Medium/High/Critical)
- Impact if materialised
- Time to impact (days)
- Specific mitigation strategy
- Owner (which agent should address this)

Present as a risk register table in markdown.
"""
        result = await self._call_fresh(prompt)
        await self._log("risk_assessment_complete", "Risk assessment complete", project.id)
        return result

    async def capacity_forecast(self, project: Project) -> str:
        """Forecast capacity and resource needs."""
        await self._log("capacity_forecast", "Forecasting capacity needs", project.id)
        prompt = f"""
Forecast capacity and resource requirements for the next 30/60/90 days for '{project.name}'.

Current workload:
- In progress: {project.metrics.tasks_in_progress} tasks
- Pending: {project.metrics.tasks_total - project.metrics.tasks_completed - project.metrics.tasks_in_progress} tasks
- Active integrations: {len(project.integrations)}
- Active strategies: {len(project.strategies)}

Targets: {', '.join(project.targets)}

Provide:
1. Capacity outlook by time period (30/60/90d)
2. Specific tools or resources that will be needed and when
3. Skill gaps or capability requirements
4. Budget/resource implications
5. Actions to take now to secure future capacity
"""
        result = await self._call_fresh(prompt)
        await self._log("capacity_forecast_complete", "Capacity forecast complete", project.id)
        return result

    async def status_report(self, project: Project) -> str:
        if project.forecast:
            days = (project.forecast.predicted_completion - datetime.utcnow()).days if project.forecast.predicted_completion else "?"
            return f"Forecaster: Completion in ~{days} days (confidence: {project.forecast.confidence:.0%}). {len(project.forecast.risks)} active risks."
        return "Forecaster: No forecast generated yet."
