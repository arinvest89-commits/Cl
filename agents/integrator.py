"""Integration Agent — identifies, evaluates and implements tools and resources."""
from __future__ import annotations
import json
from datetime import datetime
from .base import BaseAgent
from core.models import AgentRole, Project
from core.memory import MemoryManager


class IntegrationAgent(BaseAgent):
    SYSTEM_PROMPT = """You are an Integration Specialist in an AI project management team.

Your responsibilities:
- Identify tools, APIs, services, and resources that will accelerate project success
- Evaluate tools for fit, cost, complexity, and value
- Design and implement integration plans
- Manage the integration lifecycle: identify → evaluate → approve → implement → monitor
- Ensure all integrations are documented, tested, and maintainable
- Remove or replace integrations that are no longer adding value
- Monitor integration health and performance

Integration principles:
- Every tool must justify its complexity cost with clear value
- Prefer existing tools that are already available over new ones
- Integration failures should fail gracefully, never silently
- Document all integrations thoroughly so any agent can understand them
- Think ecosystem: how do tools work together, not just individually
"""

    def __init__(self, memory: MemoryManager, model: str = "claude-sonnet-4-6"):
        super().__init__(AgentRole.INTEGRATOR, "Integration Specialist", memory, model, temperature=0.4)

    async def handle_directive(self, directive: str, project: Project, **kwargs) -> str:
        await self._log("integration_directive", f"Processing: {directive[:80]}", project.id)
        existing = "\n".join(
            f"- {i.get('name', 'Unknown')}: {i.get('description', '')[:80]}"
            for i in project.integrations[:10]
        ) or "None yet"

        prompt = f"""
PROJECT: {project.name}
Goals: {', '.join(project.goals[:3])}
Current integrations:
{existing}

INTEGRATION DIRECTIVE: {directive}

Provide a specific integration response:
1. What tools/resources are needed
2. Evaluation of each (pros/cons/cost/complexity)
3. Implementation approach
4. Integration architecture
5. Testing and validation plan
6. Ongoing maintenance requirements
"""
        result = await self._call(prompt)
        await self._log("integration_directive_handled", f"Handled: {directive[:60]}", project.id, success=True)
        return result

    async def discover_tools(self, project: Project) -> list[dict]:
        """Identify tools and resources that would benefit the project."""
        await self._log("tool_discovery", "Discovering relevant tools and resources", project.id)
        prompt = f"""
Identify the most valuable tools, APIs, and resources for this project:

PROJECT: {project.name}
{project.outline[:500]}

GOALS: {', '.join(project.goals)}
TARGETS: {', '.join(project.targets)}
EXISTING INTEGRATIONS: {', '.join(i.get('name', '') for i in project.integrations) or 'None'}

Identify 5-8 high-value tools/resources. Return ONLY JSON array:
[
  {{
    "name": "Tool name",
    "type": "api|service|library|platform|agent",
    "description": "What it does",
    "value_proposition": "How it helps this specific project",
    "estimated_impact": "high|medium|low",
    "complexity": "simple|moderate|complex",
    "cost": "free|low|medium|high",
    "implementation_time_days": 2,
    "priority": "immediate|short_term|long_term"
  }}
]
"""
        raw = await self._call_fresh(prompt)
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            tools = json.loads(raw[start:end])
        except Exception:
            tools = []
        await self.memory.agent_remember(
            self.role.value,
            f"discovered_tools_{project.id}",
            [t.get("name") for t in tools],
        )
        await self._log("tool_discovery_complete", f"Discovered {len(tools)} tools", project.id)
        return tools

    async def create_integration_plan(self, project: Project, tool: dict) -> dict:
        """Create an implementation plan for integrating a specific tool."""
        await self._log("integration_plan", f"Creating plan for: {tool.get('name', 'Unknown')}", project.id)
        prompt = f"""
Create a detailed integration plan for adding '{tool.get("name")}' to project '{project.name}'.

TOOL DETAILS:
{json.dumps(tool, indent=2)}

PROJECT CONTEXT:
Goals: {', '.join(project.goals[:3])}
Existing integrations: {', '.join(i.get('name', '') for i in project.integrations)}

Return ONLY JSON:
{{
  "name": "{tool.get('name')}",
  "type": "{tool.get('type', 'service')}",
  "description": "{tool.get('description', '')}",
  "status": "planned",
  "implementation_steps": ["step1", "step2"],
  "configuration_required": ["config item 1"],
  "testing_criteria": ["test 1", "test 2"],
  "success_metrics": ["metric 1"],
  "rollback_plan": "how to undo if needed",
  "estimated_days": 3,
  "owner": "integrator",
  "added_at": "{datetime.utcnow().isoformat()}"
}}
"""
        raw = await self._call_fresh(prompt)
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            plan = json.loads(raw[start:end])
        except Exception:
            plan = {
                "name": tool.get("name", "Unknown"),
                "type": tool.get("type", "service"),
                "description": tool.get("description", ""),
                "status": "planned",
                "added_at": datetime.utcnow().isoformat(),
            }
        await self._log("integration_plan_complete", f"Plan for {tool.get('name')} created", project.id)
        return plan

    async def implement_integration(self, project: Project, integration: dict) -> str:
        """Provide detailed implementation guidance for an integration."""
        await self._log("implement_integration", f"Implementing: {integration.get('name')}", project.id)
        prompt = f"""
Provide step-by-step implementation guidance for integrating '{integration.get("name")}' into project '{project.name}'.

Integration plan:
{json.dumps(integration, indent=2)}

For each implementation step provide:
1. Exact action to take
2. Expected output/result
3. How to verify it worked
4. Common issues and solutions

Also provide:
- Configuration code samples (where applicable)
- Testing commands
- Monitoring setup
- Documentation template
"""
        result = await self._call_fresh(prompt)
        await self._log("integration_implemented", f"Implementation guide for {integration.get('name')}", project.id)
        return result

    async def integration_health_check(self, project: Project) -> dict:
        """Check the health of all current integrations."""
        await self._log("health_check", "Running integration health checks", project.id)
        if not project.integrations:
            return {"status": "ok", "active": 0, "issues": []}

        results = {"status": "ok", "active": len(project.integrations), "issues": []}
        for integration in project.integrations:
            if integration.get("status") not in ("active", "planned"):
                results["issues"].append({
                    "integration": integration.get("name"),
                    "issue": f"Status is '{integration.get('status', 'unknown')}'"
                })
        if results["issues"]:
            results["status"] = "degraded"
        await self._log("health_check_complete", f"Health check: {results['status']}", project.id)
        return results

    async def status_report(self, project: Project) -> str:
        active = sum(1 for i in project.integrations if i.get("status") == "active")
        planned = sum(1 for i in project.integrations if i.get("status") == "planned")
        return f"Integrator: {active} active, {planned} planned integrations. Total: {len(project.integrations)}."
