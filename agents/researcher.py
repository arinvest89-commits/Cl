"""Research & Analysis Agent — market research, data analysis, competitive intel."""
from __future__ import annotations
from .base import BaseAgent
from core.models import AgentRole, Project
from core.memory import MemoryManager


class ResearchAgent(BaseAgent):
    SYSTEM_PROMPT = """You are a senior Research Analyst in an AI project management team.

Your responsibilities:
- Conduct thorough market research and competitive analysis for projects
- Analyse project data, KPIs and performance metrics
- Identify industry trends, threats, and opportunities relevant to the project
- Synthesise findings into clear, actionable intelligence reports
- Surface insights that inform strategy and day-to-day decisions
- Monitor relevant market signals continuously

When responding:
- Always cite the type of data/sources you are drawing upon
- Structure findings with: Summary → Key Findings → Implications → Recommended Actions
- Quantify where possible; flag assumptions clearly
- Be direct and business-focused
"""

    def __init__(self, memory: MemoryManager, model: str = "claude-sonnet-4-6"):
        super().__init__(AgentRole.RESEARCHER, "Research Analyst", memory, model, temperature=0.4)

    async def handle_directive(self, directive: str, project: Project, **kwargs) -> str:
        await self._log("research_directive", f"Processing: {directive[:80]}...", project.id)
        prompt = f"""
PROJECT CONTEXT:
Name: {project.name}
Outline: {project.outline}
Goals: {', '.join(project.goals)}
Targets: {', '.join(project.targets)}
Current Status: {project.status.value}

DIRECTIVE: {directive}

Provide a thorough research analysis to address this directive. Include:
1. Relevant market/industry context
2. Data analysis and key findings
3. Competitive landscape (if applicable)
4. Specific implications for this project
5. Actionable recommendations
"""
        result = await self._call(prompt)
        await self.memory.agent_remember(self.role.value, f"last_research_{project.id}", result[:500])
        await self._log("research_complete", f"Research delivered for: {directive[:60]}", project.id, success=True)
        return result

    async def analyse_project_performance(self, project: Project) -> str:
        await self._log("performance_analysis", "Analysing project performance metrics", project.id)
        metrics = project.compute_metrics()
        prompt = f"""
Analyse the following project performance data and provide insights:

PROJECT: {project.name}
Status: {project.status.value}
Completion Rate: {metrics.completion_rate:.1f}%
Tasks: {metrics.tasks_total} total | {metrics.tasks_completed} done | {metrics.tasks_in_progress} active | {metrics.tasks_blocked} blocked
Health Score: {metrics.health_score:.0f}/100
Active Strategies: {len(project.strategies)}
Integrations: {len(project.integrations)}

Goals: {chr(10).join(f'- {g}' for g in project.goals)}
Targets: {chr(10).join(f'- {t}' for t in project.targets)}

Provide:
1. Performance assessment (what's working, what isn't)
2. Gap analysis (distance from goals/targets)
3. Root cause analysis for any issues
4. Recommended immediate actions
"""
        result = await self._call_fresh(prompt)
        await self._log("performance_analysis_complete", "Performance analysis delivered", project.id)
        return result

    async def market_scan(self, project: Project) -> str:
        await self._log("market_scan", "Running market scan", project.id)
        prompt = f"""
Conduct a comprehensive market scan relevant to this project:

PROJECT: {project.name}
{project.outline}

Goals: {', '.join(project.goals)}

Analyse:
1. Current market trends affecting this project
2. Emerging opportunities in the next 30-90 days
3. Key threats or risks in the market
4. Competitor or comparable project activities
5. Technology or tool developments worth watching
6. Customer/user behaviour trends (if applicable)

Format as an executive briefing.
"""
        result = await self._call_fresh(prompt)
        await self._log("market_scan_complete", "Market scan complete", project.id)
        return result

    async def status_report(self, project: Project) -> str:
        return await self.analyse_project_performance(project)
