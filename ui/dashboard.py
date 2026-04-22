"""
Main terminal UI dashboard built with Rich + prompt_toolkit.
Provides a split-pane interface: project status, agent feed, chat.
"""
from __future__ import annotations
import asyncio
import os
import sys
from collections import deque
from datetime import datetime
from typing import Optional, Callable

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.progress import Progress, BarColumn, TextColumn
from rich.rule import Rule
from rich.align import Align
from rich import box
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from core.models import (
    Project, ChatMessage, AgentActivity, ApprovalRequest,
    MessageRole, AgentRole, ProjectStatus, TaskStatus,
)


ROLE_COLOURS = {
    AgentRole.ORCHESTRATOR: "bold cyan",
    AgentRole.RESEARCHER: "bold blue",
    AgentRole.STRATEGIST: "bold magenta",
    AgentRole.OPERATIONS: "bold green",
    AgentRole.FORECASTER: "bold yellow",
    AgentRole.QA: "bold red",
    AgentRole.INTEGRATOR: "bold orange3",
    AgentRole.EXTERNAL: "bold white",
}

ROLE_ICONS = {
    AgentRole.ORCHESTRATOR: "PM",
    AgentRole.RESEARCHER: "RA",
    AgentRole.STRATEGIST: "SL",
    AgentRole.OPERATIONS: "OM",
    AgentRole.FORECASTER: "FA",
    AgentRole.QA: "QA",
    AgentRole.INTEGRATOR: "IS",
}

STATUS_COLOURS = {
    ProjectStatus.PLANNING: "yellow",
    ProjectStatus.ACTIVE: "green",
    ProjectStatus.PAUSED: "orange3",
    ProjectStatus.COMPLETED: "bright_green",
    ProjectStatus.FAILED: "red",
}


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[:max_len - 3] + "..."


class AgentTeamDashboard:
    """
    Full-featured terminal dashboard for the agent team.
    Left: project status + metrics
    Centre: agent activity feed
    Right: chat window
    Bottom: approval queue
    """

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self._messages: deque[ChatMessage] = deque(maxlen=150)
        self._activities: deque[AgentActivity] = deque(maxlen=100)
        self._approvals: list[ApprovalRequest] = []
        self._current_project: Optional[Project] = None
        self._projects: list[Project] = []
        self._acp_statuses: dict[str, str] = {}
        self._live: Optional[Live] = None
        self._running = False

    # ── Data updates (called by orchestrator callbacks) ───────────────────────

    def add_message(self, msg: ChatMessage) -> None:
        self._messages.append(msg)

    def add_activity(self, activity: AgentActivity) -> None:
        self._activities.append(activity)

    def add_approval(self, approval: ApprovalRequest) -> None:
        existing = next((a for a in self._approvals if a.id == approval.id), None)
        if existing:
            self._approvals.remove(existing)
        self._approvals.append(approval)

    def set_project(self, project: Project) -> None:
        self._current_project = project

    def set_projects(self, projects: list[Project]) -> None:
        self._projects = projects

    def set_acp_statuses(self, statuses: dict[str, str]) -> None:
        self._acp_statuses = statuses

    # ── Render panels ─────────────────────────────────────────────────────────

    def _render_header(self) -> Panel:
        pending_count = sum(1 for a in self._approvals if a.status.value == "pending")
        title = Text()
        title.append("  AGENT TEAM  ", style="bold white on dark_blue")
        title.append("  Project Management & Execution System  ", style="dim white")
        if pending_count:
            title.append(f"  {pending_count} PENDING APPROVAL(S)  ", style="bold white on red")
        return Panel(Align.center(title), style="dark_blue", height=3)

    def _render_project_panel(self) -> Panel:
        p = self._current_project
        if not p:
            content = Text("No active project.\nType: /new to create one", style="dim")
            return Panel(content, title="[bold]Project Status[/bold]", border_style="dim blue", height=20)

        p.compute_metrics()
        m = p.metrics

        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column("Key", style="dim cyan", width=14)
        table.add_column("Value")

        status_color = STATUS_COLOURS.get(p.status, "white")
        table.add_row("Name", Text(p.name, style="bold white"))
        table.add_row("Status", Text(p.status.value.upper(), style=f"bold {status_color}"))
        table.add_row("Progress", f"{m.completion_rate:.1f}%")
        table.add_row("Health", f"{m.health_score:.0f}/100")
        table.add_row("Tasks", f"{m.tasks_completed}/{m.tasks_total}")
        table.add_row("In Progress", str(m.tasks_in_progress))
        table.add_row("Blocked", Text(str(m.tasks_blocked), style="red" if m.tasks_blocked else "green"))
        table.add_row("Strategies", str(len(p.strategies)))
        table.add_row("Integrations", str(len(p.integrations)))

        if p.forecast:
            days = (p.forecast.predicted_completion - datetime.utcnow()).days if p.forecast.predicted_completion else "?"
            table.add_row("ETA", f"~{days} days ({p.forecast.confidence:.0%})")

        # Progress bar
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=20, style="green", complete_style="bright_green"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            expand=False,
        )
        task_id = progress.add_task("Progress", total=100, completed=m.completion_rate)

        # Goals summary
        goals_text = Text()
        for goal in p.goals[:3]:
            goals_text.append(f"• {_truncate(goal, 28)}\n", style="dim white")

        content = Text()
        from rich.console import Group
        return Panel(
            Group(table, Rule(style="dim"), progress, Rule(style="dim"), goals_text),
            title=f"[bold cyan]Project Status[/bold cyan]",
            border_style="cyan",
        )

    def _render_activity_feed(self) -> Panel:
        table = Table(
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="bold dim",
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Time", style="dim", width=8)
        table.add_column("Agent", width=4)
        table.add_column("Action", width=18)
        table.add_column("Description")

        recent = list(self._activities)[-20:]
        for a in reversed(recent):
            color = ROLE_COLOURS.get(a.agent, "white")
            icon = ROLE_ICONS.get(a.agent, "??")
            time_str = a.timestamp.strftime("%H:%M:%S")
            success_icon = "" if a.success else "[red]![/red] "
            table.add_row(
                time_str,
                f"[{color}]{icon}[/{color}]",
                _truncate(a.action, 18),
                f"{success_icon}{_truncate(a.description, 50)}",
            )
        return Panel(
            table,
            title="[bold]Agent Activity Feed[/bold]",
            border_style="blue",
        )

    def _render_chat_panel(self) -> Panel:
        lines = []
        recent = list(self._messages)[-30:]
        for msg in recent:
            ts = msg.timestamp.strftime("%H:%M")
            if msg.role == MessageRole.USER:
                lines.append(Text())
                lines.append(Text(f"[{ts}] YOU", style="bold yellow"))
                lines.append(Text(msg.content, style="white"))
            else:
                agent = msg.agent or AgentRole.ORCHESTRATOR
                color = ROLE_COLOURS.get(agent, "cyan")
                icon = ROLE_ICONS.get(agent, "AG")
                lines.append(Text())
                lines.append(Text(f"[{ts}] {icon} {agent.value.upper()}", style=color))
                # Truncate long messages in the panel
                content = msg.content[:400] + ("..." if len(msg.content) > 400 else "")
                lines.append(Text(content, style="dim white"))

        if not lines:
            lines = [Text("No messages yet. Start chatting below!", style="dim")]

        from rich.console import Group
        return Panel(
            Group(*lines[-25:]),
            title="[bold]Chat[/bold]",
            border_style="green",
        )

    def _render_approvals_panel(self) -> Panel:
        pending = [a for a in self._approvals if a.status.value == "pending"]
        if not pending:
            return Panel(
                Text("No pending approvals.", style="dim green"),
                title="[bold]Approvals[/bold]",
                border_style="dim",
                height=5,
            )
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1), expand=True)
        table.add_column("ID", width=8)
        table.add_column("Decision", width=30)
        table.add_column("Impact", width=8)
        table.add_column("Options")
        for a in pending[-5:]:
            impact_color = {"low": "green", "medium": "yellow", "high": "red", "critical": "bold red"}.get(a.impact_level, "white")
            table.add_row(
                a.id,
                _truncate(a.title, 30),
                Text(a.impact_level.upper(), style=impact_color),
                " | ".join(a.options[:3]),
            )
        return Panel(
            table,
            title=f"[bold red]Pending Approvals ({len(pending)})[/bold red]",
            border_style="red",
            height=6 + len(pending),
        )

    def _render_acp_panel(self) -> Panel:
        if not self._acp_statuses:
            return Panel(Text("ACP: No external agents configured.", style="dim"), title="External Agents", border_style="dim", height=3)
        parts = []
        for name, status in self._acp_statuses.items():
            color = {"online": "green", "offline": "red", "timeout": "yellow", "unknown": "dim"}.get(status, "dim")
            parts.append(Text(f"  {name}: ", style="white") + Text(status.upper(), style=color))
        from rich.console import Group
        return Panel(Group(*parts), title="[bold]External Agents (ACP)[/bold]", border_style="dim blue", height=3 + len(self._acp_statuses))

    def _build_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="approvals"),
            Layout(name="footer", size=3),
        )
        layout["main"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="centre", ratio=2),
            Layout(name="right", ratio=2),
        )
        layout["header"].update(self._render_header())
        layout["left"].update(self._render_project_panel())
        layout["centre"].update(self._render_activity_feed())
        layout["right"].update(self._render_chat_panel())
        layout["approvals"].update(self._render_approvals_panel())
        layout["footer"].update(
            Panel(
                Text(
                    "Commands: /new  /open <id>  /status  /daily  /approve <id>  /reject <id>  "
                    "/research  /forecast  /test  /tools  /agents  /help  |  Ctrl+C to exit",
                    style="dim",
                ),
                style="dim",
                height=3,
            )
        )
        return layout

    def refresh(self) -> None:
        if self._live:
            self._live.update(self._build_layout())

    async def run_display(self) -> None:
        """Start the live display (non-blocking background refresh)."""
        self._running = True
        with Live(
            self._build_layout(),
            console=self.console,
            refresh_per_second=2,
            screen=True,
        ) as live:
            self._live = live
            while self._running:
                live.update(self._build_layout())
                await asyncio.sleep(0.5)

    def stop(self) -> None:
        self._running = False
        self._live = None
