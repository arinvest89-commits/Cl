#!/usr/bin/env python3
"""
Agent Team — Project Management & Execution System
Entry point: starts the terminal UI, orchestrator, ACP server, and input loop.

Usage:
    python main.py [--no-acp] [--model MODEL]

Environment:
    ANTHROPIC_API_KEY  Required for all agent LLM calls
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from config import CONFIG
from core.memory import MemoryManager
from core.orchestrator import Orchestrator
from core.acp import ACPManager
from core.approval import ApprovalManager
from core.models import (
    ApprovalRequest, ApprovalStatus, AgentRole,
    ChatMessage, MessageRole, AgentActivity,
)
from ui.dashboard import AgentTeamDashboard

console = Console()

PROMPT_STYLE = Style.from_dict({
    "prompt": "bold cyan",
})

HELP_TEXT = """
[bold cyan]Agent Team — Command Reference[/bold cyan]

[bold]Project Management[/bold]
  /new                    Create a new project (guided setup)
  /open <project_id>      Load and activate a project
  /list                   List all projects
  /status                 Full project status + team report
  /daily                  Run daily management cycle

[bold]Agent Directives[/bold]
  /research <topic>       Research Analyst: market/competitive research
  /strategy <directive>   Strategy Lead: create or adjust strategy
  /forecast               Forecasting Analyst: refresh forecast
  /test                   QA Engineer: run test plan
  /tools                  Integration Specialist: discover tools
  /ops <directive>        Operations Manager: operational directive
  /standup                Generate daily standup report
  /unblock <task_id>      Operations: unblock a specific task

[bold]Approvals[/bold]
  /approvals              List pending approvals
  /approve <id> [notes]   Approve a decision
  /reject <id> [notes]    Reject a decision

[bold]External Agents (ACP)[/bold]
  /agents                 Show external agent status
  /ping                   Ping all external agents
  /send <agent> <message> Send a message to an external agent

[bold]System[/bold]
  /help                   Show this help
  /clear                  Clear chat history display
  /exit or /quit          Exit the system

[dim]Any other text is sent directly to the Project Manager as a chat message.[/dim]
"""


class AgentTeamApp:
    """Main application controller."""

    def __init__(self, model: str = "claude-sonnet-4-6", enable_acp: bool = True):
        self.model = model
        self.enable_acp = enable_acp
        self.memory = MemoryManager()
        self.orchestrator = Orchestrator(self.memory, model)
        self.dashboard = AgentTeamDashboard(console)
        self.approval_manager = ApprovalManager(
            self.memory,
            auto_approve_threshold=CONFIG.get("approval", {}).get("auto_approve_threshold", "low"),
            notify_callback=self.dashboard.add_approval,
        )

        acp_cfg = CONFIG.get("acp", {})
        self.acp = ACPManager(
            host=acp_cfg.get("host", "0.0.0.0"),
            port=acp_cfg.get("port", 8765),
            known_agents=acp_cfg.get("known_agents", {}),
            message_handler=self.orchestrator.handle_acp_message if enable_acp else None,
        )

        self._active_project_id: Optional[str] = None
        self._prompt_session = PromptSession(style=PROMPT_STYLE)
        self._running = False

        # Wire callbacks
        self.orchestrator.on_message(self.dashboard.add_message)
        self.orchestrator.on_activity(self.dashboard.add_activity)
        self.orchestrator.on_approval_needed(self.dashboard.add_approval)

    # ── Startup ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        # Load existing projects into dashboard
        projects = await self.memory.list_projects()
        self.dashboard.set_projects(projects)
        if projects:
            self._active_project_id = projects[0].id
            self.dashboard.set_project(projects[0])

        # Load approvals
        approvals = await self.memory.load_approvals(pending_only=True)
        for a in approvals:
            self.dashboard.add_approval(a)

        # Load recent activity
        activities = await self.memory.load_activity_log(limit=30)
        for a in activities:
            self.dashboard.add_activity(a)

        # Load recent chat
        messages = await self.memory.load_chat_history(limit=50)
        for m in messages:
            self.dashboard.add_message(m)

        # Start ACP
        if self.enable_acp:
            await self.acp.start()
            statuses = await self.acp.discover_agents()
            self.dashboard.set_acp_statuses(self.acp.agent_statuses())

        self._running = True

    async def stop(self) -> None:
        self._running = False
        self.dashboard.stop()
        await self.acp.stop()

    # ── Command handlers ──────────────────────────────────────────────────────

    async def _cmd_new_project(self) -> None:
        console.print()
        console.print(Panel("[bold cyan]Create New Project[/bold cyan]", expand=False))
        name = await self._prompt_session.prompt_async("Project name: ")
        if not name.strip():
            console.print("[red]Name cannot be empty.[/red]")
            return
        console.print("[dim]Describe the project (multi-line, enter a blank line to finish):[/dim]")
        outline_lines = []
        while True:
            line = await self._prompt_session.prompt_async("> ")
            if not line.strip():
                break
            outline_lines.append(line)
        outline = " ".join(outline_lines)

        console.print("[dim]Enter project goals (one per line, blank to finish):[/dim]")
        goals = []
        while True:
            g = await self._prompt_session.prompt_async("Goal: ")
            if not g.strip():
                break
            goals.append(g.strip())

        console.print("[dim]Enter project targets (one per line, blank to finish):[/dim]")
        targets = []
        while True:
            t = await self._prompt_session.prompt_async("Target: ")
            if not t.strip():
                break
            targets.append(t.strip())

        if not goals:
            goals = ["Successfully deliver the project"]
        if not targets:
            targets = ["Complete all milestones on time"]

        console.print("[cyan]Initialising project — this may take a moment...[/cyan]")
        project = await self.orchestrator.initialise_project(name, outline, goals, targets)
        self._active_project_id = project.id
        self.dashboard.set_project(project)
        projects = await self.memory.list_projects()
        self.dashboard.set_projects(projects)
        console.print(f"[green]Project '{name}' created with ID: {project.id}[/green]")

    async def _cmd_open_project(self, project_id: str) -> None:
        project = await self.memory.load_project(project_id.strip())
        if not project:
            # Try partial match
            projects = await self.memory.list_projects()
            matches = [p for p in projects if project_id.lower() in p.name.lower() or p.id.startswith(project_id)]
            if matches:
                project = matches[0]
            else:
                console.print(f"[red]Project '{project_id}' not found.[/red]")
                return
        self._active_project_id = project.id
        self.dashboard.set_project(project)
        console.print(f"[green]Opened project: {project.name}[/green]")

    async def _cmd_list_projects(self) -> None:
        projects = await self.memory.list_projects()
        if not projects:
            console.print("[dim]No projects found. Use /new to create one.[/dim]")
            return
        from rich.table import Table
        from rich import box
        table = Table(box=box.ROUNDED, title="Projects")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Status")
        table.add_column("Progress")
        table.add_column("Tasks")
        table.add_column("Updated")
        for p in projects:
            p.compute_metrics()
            active = "[green]*[/green] " if p.id == self._active_project_id else "  "
            table.add_row(
                p.id,
                f"{active}{p.name}",
                p.status.value,
                f"{p.metrics.completion_rate:.0f}%",
                f"{p.metrics.tasks_completed}/{p.metrics.tasks_total}",
                p.updated_at.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)

    async def _cmd_status(self) -> None:
        project = await self._get_active_project()
        if not project:
            return
        console.print("[cyan]Generating full team status report...[/cyan]")
        summary = await self.orchestrator.synthesise_status(project)
        console.print(Panel(summary, title=f"[bold]Status Report — {project.name}[/bold]", border_style="cyan"))

    async def _cmd_daily_cycle(self) -> None:
        project = await self._get_active_project()
        if not project:
            return
        console.print("[cyan]Running daily management cycle...[/cyan]")
        await self.orchestrator.run_daily_cycle(project.id)
        project = await self.memory.load_project(project.id)
        if project:
            self.dashboard.set_project(project)

    async def _cmd_research(self, topic: str) -> None:
        project = await self._get_active_project()
        if not project:
            return
        directive = topic or "Conduct a full market scan and performance analysis"
        console.print(f"[cyan]Research Analyst working on: {directive[:60]}...[/cyan]")
        result = await self.orchestrator.researcher.handle_directive(directive, project)
        console.print(Panel(result, title="[bold blue]Research Analyst[/bold blue]", border_style="blue"))

    async def _cmd_strategy(self, directive: str) -> None:
        project = await self._get_active_project()
        if not project:
            return
        console.print("[cyan]Strategy Lead processing directive...[/cyan]")
        result = await self.orchestrator.strategist.handle_directive(directive, project)
        console.print(Panel(result, title="[bold magenta]Strategy Lead[/bold magenta]", border_style="magenta"))

    async def _cmd_forecast(self) -> None:
        project = await self._get_active_project()
        if not project:
            return
        console.print("[cyan]Forecasting Analyst generating forecast...[/cyan]")
        forecast = await self.orchestrator.forecaster.generate_forecast(project)
        result = await self.orchestrator.forecaster.handle_directive("Provide full project forecast", project)
        await self.memory.save_project(project)
        self.dashboard.set_project(project)
        console.print(Panel(result, title="[bold yellow]Forecast[/bold yellow]", border_style="yellow"))

    async def _cmd_test(self) -> None:
        project = await self._get_active_project()
        if not project:
            return
        console.print("[cyan]QA Engineer running tests...[/cyan]")
        plan = await self.orchestrator.qa.create_test_plan(project)
        console.print(Panel(plan, title="[bold red]QA Test Plan[/bold red]", border_style="red"))

    async def _cmd_tools(self) -> None:
        project = await self._get_active_project()
        if not project:
            return
        console.print("[cyan]Integration Specialist discovering tools...[/cyan]")
        tools = await self.orchestrator.integrator.discover_tools(project)
        from rich.table import Table
        from rich import box
        table = Table(box=box.ROUNDED, title="Recommended Tools & Integrations")
        table.add_column("Tool", style="bold")
        table.add_column("Type")
        table.add_column("Impact")
        table.add_column("Priority")
        table.add_column("Value")
        for t in tools:
            impact_color = {"high": "green", "medium": "yellow", "low": "dim"}.get(t.get("estimated_impact", "medium"), "white")
            table.add_row(
                t.get("name", ""),
                t.get("type", ""),
                Text(t.get("estimated_impact", ""), style=impact_color),
                t.get("priority", ""),
                t.get("value_proposition", "")[:50],
            )
        console.print(table)

    async def _cmd_ops(self, directive: str) -> None:
        project = await self._get_active_project()
        if not project:
            return
        console.print("[cyan]Operations Manager processing...[/cyan]")
        result = await self.orchestrator.operations.handle_directive(directive, project)
        console.print(Panel(result, title="[bold green]Operations Manager[/bold green]", border_style="green"))

    async def _cmd_standup(self) -> None:
        project = await self._get_active_project()
        if not project:
            return
        result = await self.orchestrator.operations.daily_standup(project)
        console.print(Panel(result, title="[bold green]Daily Standup[/bold green]", border_style="green"))

    async def _cmd_unblock(self, task_id: str) -> None:
        project = await self._get_active_project()
        if not project:
            return
        result = await self.orchestrator.operations.unblock(project, task_id.strip())
        console.print(Panel(result, title="[bold]Unblock Proposal[/bold]", border_style="orange3"))

    async def _cmd_approvals(self) -> None:
        pending = await self.memory.load_approvals(pending_only=True)
        if not pending:
            console.print("[green]No pending approvals.[/green]")
            return
        from rich.table import Table
        from rich import box
        table = Table(box=box.ROUNDED, title="Pending Approvals")
        table.add_column("ID", width=8)
        table.add_column("Title")
        table.add_column("Impact")
        table.add_column("Options")
        table.add_column("Recommended")
        for a in pending:
            table.add_row(
                a.id,
                a.title,
                Text(a.impact_level.upper(), style={"low": "green", "medium": "yellow", "high": "red"}.get(a.impact_level, "white")),
                " | ".join(a.options),
                a.recommended_option or "",
            )
        console.print(table)
        console.print("\n[dim]Full detail: /approve <id> [notes]  or  /reject <id> [notes][/dim]")
        # Show full description for each
        for a in pending[:3]:
            console.print(Panel(a.description, title=f"[bold]{a.title}[/bold] [{a.id}]", border_style="yellow"))

    async def _cmd_approve(self, parts: list[str]) -> None:
        if not parts:
            console.print("[red]Usage: /approve <id> [notes][/red]")
            return
        approval_id = parts[0]
        notes = " ".join(parts[1:]) if len(parts) > 1 else ""

        # Show options
        approvals = await self.memory.load_approvals()
        approval = next((a for a in approvals if a.id == approval_id), None)
        if not approval:
            console.print(f"[red]Approval {approval_id} not found.[/red]")
            return

        console.print(Panel(
            f"[bold]{approval.title}[/bold]\n\n{approval.description[:500]}\n\nOptions: {', '.join(approval.options)}",
            title="Approval Request",
            border_style="yellow",
        ))
        if approval.options:
            choice = await self._prompt_session.prompt_async(
                f"Choose option (default: '{approval.recommended_option}'): "
            )
            if not choice.strip():
                choice = approval.recommended_option or approval.options[0]
        else:
            choice = "Approved"

        result = await self.orchestrator.resolve_approval(approval_id, choice, notes)
        console.print(f"[green]{result}[/green]")

    async def _cmd_reject(self, parts: list[str]) -> None:
        if not parts:
            console.print("[red]Usage: /reject <id> [reason][/red]")
            return
        approval_id = parts[0]
        notes = " ".join(parts[1:]) if len(parts) > 1 else "Rejected by user"
        result = await self.orchestrator.resolve_approval(approval_id, "Reject and revise", notes)
        console.print(f"[yellow]{result}[/yellow]")

    async def _cmd_agents(self) -> None:
        statuses = self.acp.agent_statuses()
        if not statuses:
            console.print("[dim]No external agents configured.[/dim]")
            return
        from rich.table import Table
        from rich import box
        table = Table(box=box.ROUNDED, title="External Agents (ACP)")
        table.add_column("Agent")
        table.add_column("Status")
        for name, status in statuses.items():
            color = {"online": "green", "offline": "red", "timeout": "yellow", "unknown": "dim"}.get(status, "white")
            table.add_row(name, Text(status.upper(), style=color))
        console.print(table)

    async def _cmd_ping(self) -> None:
        console.print("[cyan]Pinging external agents...[/cyan]")
        results = await self.acp.discover_agents()
        self.dashboard.set_acp_statuses(self.acp.agent_statuses())
        for name, online in results.items():
            icon = "[green]ONLINE[/green]" if online else "[red]OFFLINE[/red]"
            console.print(f"  {name}: {icon}")

    async def _cmd_send_to_agent(self, parts: list[str]) -> None:
        if len(parts) < 2:
            console.print("[red]Usage: /send <agent_name> <message>[/red]")
            return
        agent_name = parts[0]
        message = " ".join(parts[1:])
        project_id = self._active_project_id
        result = await self.acp.send_to(
            agent_name, "broadcast",
            {"message": message, "project_id": project_id},
        )
        if result:
            console.print(f"[green]Sent to {agent_name}. Response: {result.payload}[/green]")
        else:
            console.print(f"[yellow]Message sent to {agent_name} (no response or agent offline).[/yellow]")

    # ── Input processing ──────────────────────────────────────────────────────

    async def process_input(self, raw: str) -> bool:
        """Process one line of user input. Returns False to exit."""
        line = raw.strip()
        if not line:
            return True

        if line.startswith("/"):
            parts = line[1:].split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            arg_parts = args.split() if args else []

            if cmd in ("exit", "quit", "q"):
                return False
            elif cmd == "help":
                console.print(HELP_TEXT)
            elif cmd == "new":
                await self._cmd_new_project()
            elif cmd == "open":
                await self._cmd_open_project(args)
            elif cmd == "list":
                await self._cmd_list_projects()
            elif cmd == "status":
                await self._cmd_status()
            elif cmd == "daily":
                await self._cmd_daily_cycle()
            elif cmd == "research":
                await self._cmd_research(args)
            elif cmd == "strategy":
                await self._cmd_strategy(args)
            elif cmd == "forecast":
                await self._cmd_forecast()
            elif cmd == "test":
                await self._cmd_test()
            elif cmd == "tools":
                await self._cmd_tools()
            elif cmd == "ops":
                await self._cmd_ops(args or "Give me a current operational overview")
            elif cmd == "standup":
                await self._cmd_standup()
            elif cmd == "unblock":
                await self._cmd_unblock(args)
            elif cmd == "approvals":
                await self._cmd_approvals()
            elif cmd == "approve":
                await self._cmd_approve(arg_parts)
            elif cmd == "reject":
                await self._cmd_reject(arg_parts)
            elif cmd == "agents":
                await self._cmd_agents()
            elif cmd == "ping":
                await self._cmd_ping()
            elif cmd == "send":
                await self._cmd_send_to_agent(arg_parts)
            elif cmd == "clear":
                console.clear()
            else:
                console.print(f"[red]Unknown command: /{cmd}[/red] — type /help for commands")
        else:
            # Direct chat to orchestrator
            response = await self.orchestrator.chat(line, self._active_project_id)
            # Response is already emitted via callback — show it directly too
            console.print(f"\n[bold cyan]PM>[/bold cyan] {response}\n")

        return True

    async def _get_active_project(self):
        if not self._active_project_id:
            console.print("[yellow]No active project. Use /new or /open <id>.[/yellow]")
            return None
        return await self.memory.load_project(self._active_project_id)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        await self.start()
        console.clear()

        # Print banner
        banner = Text()
        banner.append("\n  AGENT TEAM  ", style="bold white on dark_blue")
        banner.append("  Project Management & Execution System\n", style="bold cyan")
        banner.append(f"  Model: {self.model}  |  ", style="dim")
        banner.append(f"ACP: {'enabled' if self.enable_acp else 'disabled'}  |  ", style="dim")
        banner.append(f"Projects loaded: {len(await self.memory.list_projects())}\n", style="dim")
        console.print(Panel(banner, border_style="cyan"))
        console.print("[dim]Type /help for commands. Type any message to chat with the Project Manager.[/dim]\n")

        try:
            with patch_stdout():
                while self._running:
                    try:
                        user_input = await self._prompt_session.prompt_async(
                            [("class:prompt", "\nYou> ")]
                        )
                        should_continue = await self.process_input(user_input)
                        if not should_continue:
                            break
                    except KeyboardInterrupt:
                        console.print("\n[dim]Use /exit to quit.[/dim]")
                    except EOFError:
                        break
        finally:
            await self.stop()
            console.print("\n[dim]Agent Team shut down. Goodbye.[/dim]")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Team — Project Management System")
    parser.add_argument("--model", default=CONFIG["agent_team"]["model"],
                        help="Claude model to use")
    parser.add_argument("--no-acp", action="store_true",
                        help="Disable ACP server (no external agent comms)")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]ERROR: ANTHROPIC_API_KEY environment variable not set.[/red]")
        console.print("[dim]Set it with: export ANTHROPIC_API_KEY=your_key_here[/dim]")
        sys.exit(1)

    app = AgentTeamApp(model=args.model, enable_acp=not args.no_acp)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
