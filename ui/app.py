"""
AgentTeamApp — the main Textual TUI.

Layout:
  ┌────────────────────────────────────────────────────────────────────┐
  │  AGENT TEAM CONTROL CENTRE                  [Project]  [Status]   │
  ├──────────────────┬────────────────────────┬───────────────────────┤
  │  AGENT ACTIVITY  │    CONVERSATION        │   PROJECT DASHBOARD   │
  │                  │                        │                       │
  │  ○ Orchestrator  │  agent_team >  Hello!  │   Status: Active      │
  │  ● Strategy      │                        │   Goals: 3            │
  │  ◐ Research      │  You > ...             │   Tasks: 5/12         │
  │  ○ Implementation│                        │                       │
  │  ○ Forecasting   │                        │   PENDING APPROVALS   │
  │  ○ QA            │                        │   [!] Budget increase │
  ├──────────────────┴────────────────────────┴───────────────────────┤
  │  > Type a message or command...                          [Send]    │
  ├────────────────────────────────────────────────────────────────────┤
  │  /help  /new-project  /status  /approve  /agents  /acp           │
  └────────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

from core.database import init_db
from core.models import AgentType, ChatMessage, DecisionInfo
from core.state import state
from ui.panels import AgentStatusWidget, ApprovalWidget, ChatMessageWidget, ProjectMetricsWidget

CSS_PATH = Path(__file__).parent / "theme.tcss"

HELP_TEXT = """**Agent Team — Command Reference**

| Command | Description |
|---------|-------------|
| `/new-project <name>` | Start a new project (you'll be prompted for details) |
| `/load-project <id>` | Switch to an existing project |
| `/projects` | List all projects |
| `/status` | Show full project status |
| `/approve <id>` | Approve a pending decision |
| `/reject <id>` | Reject a pending decision |
| `/agents` | List connected external agents |
| `/acp-register <name> <url>` | Register an external agent endpoint |
| `/tasks` | Show all tasks |
| `/strategies` | Show all strategies |
| `/forecasts` | Show all forecasts |
| `/logs` | Show recent agent logs |
| `/loop start` | Start the autonomous review loop |
| `/loop stop` | Stop the autonomous review loop |
| `/help` | Show this help |

**Chat**: Just type naturally to talk to the agent team.
"""


class AgentTeamApp(App):
    """The Agent Team Control Centre."""

    CSS_PATH = CSS_PATH
    TITLE = "Agent Team Control Centre"
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("f1", "show_help", "Help"),
        ("f2", "toggle_loop", "Toggle Loop"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._orchestrator = None
        self._agent_widgets: dict[AgentType, AgentStatusWidget] = {}
        self._pending_approvals: dict[int, DecisionInfo] = {}
        self._new_project_step: int = 0
        self._new_project_data: dict = {}
        self._loop_running = False

    # ─── Compose ─────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        # Header
        with Container(id="header"):
            yield Label("  AGENT TEAM CONTROL CENTRE", id="header-title")
            yield Label("No Project Active", id="header-project")
            yield Label("OFFLINE  ", id="header-status")

        # Body
        with Container(id="body"):
            # Left: agent status
            with Vertical(id="left-panel"):
                yield Label(" AGENT ACTIVITY", id="left-panel-title")
                with ScrollableContainer(id="agent-list"):
                    for agent_type in AgentType:
                        w = AgentStatusWidget(agent_type)
                        self._agent_widgets[agent_type] = w
                        yield w

            # Centre: chat
            with Vertical(id="centre-panel"):
                yield Label(" CONVERSATION", id="chat-title")
                with ScrollableContainer(id="chat-log"):
                    pass  # messages added dynamically
                with Horizontal(id="input-row"):
                    yield Input(placeholder="Type a message or /command ...", id="chat-input")
                    yield Button("Send", id="send-btn")

            # Right: project + approvals
            with Vertical(id="right-panel"):
                yield Label(" PROJECT DASHBOARD", id="right-panel-title")
                yield ProjectMetricsWidget()
                with ScrollableContainer(id="approval-section"):
                    yield Label(" PENDING APPROVALS", classes="section-header")

        # Footer
        with Container(id="footer"):
            yield Label(
                " /help  /new-project  /status  /approve  /agents  /acp-register",
            )

    # ─── Startup ─────────────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        """Initialise DB, agents, and subscribe to state events."""
        await init_db()
        await self._init_agents()
        self._subscribe_to_state()
        self._post_system_message(
            "Agent Team Control Centre initialised.\n\n"
            "Type `/new-project <name>` to start a project, or `/help` for all commands."
        )
        self._update_header()

    async def _init_agents(self) -> None:
        from agents.forecasting import ForecastingAgent
        from agents.implementation import ImplementationAgent
        from agents.orchestrator import OrchestratorAgent
        from agents.qa import QAAgent
        from agents.research import ResearchAgent
        from agents.strategy import StrategyAgent

        try:
            self._orchestrator = OrchestratorAgent()
            self._orchestrator.register_agent(StrategyAgent())
            self._orchestrator.register_agent(ResearchAgent())
            self._orchestrator.register_agent(ImplementationAgent())
            self._orchestrator.register_agent(ForecastingAgent())
            self._orchestrator.register_agent(QAAgent())
            self.query_one("#header-status", Label).update("ONLINE  ")
        except RuntimeError as exc:
            self.query_one("#header-status", Label).update("NO API KEY")
            self._post_system_message(f"**Warning:** {exc}")

    def _subscribe_to_state(self) -> None:
        state.subscribe("message_added", self._on_message_added)
        state.subscribe("agent_status_updated", self._on_agent_status_updated)
        state.subscribe("approval_requested", self._on_approval_requested)
        state.subscribe("approval_resolved", self._on_approval_resolved)
        state.subscribe("project_created", self._on_project_changed)
        state.subscribe("project_activated", self._on_project_changed)
        state.subscribe("project_updated", self._on_project_updated)
        state.subscribe("strategy_saved", self._on_dashboard_changed)
        state.subscribe("forecast_saved", self._on_dashboard_changed)
        state.subscribe("task_created", self._on_dashboard_changed)
        state.subscribe("task_updated", self._on_dashboard_changed)

    # ─── Input handling ──────────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self._handle_input(event.value)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            input_widget = self.query_one("#chat-input", Input)
            await self._handle_input(input_widget.value)

    async def _handle_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return

        # Clear input
        try:
            self.query_one("#chat-input", Input).value = ""
        except Exception:
            pass

        # Multi-step new project wizard
        if self._new_project_step > 0:
            await self._handle_project_wizard(text)
            return

        # Commands
        if text.startswith("/"):
            await self._handle_command(text)
        else:
            await self._handle_chat(text)

    async def _handle_command(self, text: str) -> None:
        parts = text.split(None, 2)
        cmd = parts[0].lower()

        if cmd == "/help":
            self._post_agent_message(HELP_TEXT)

        elif cmd == "/new-project":
            name = parts[1] if len(parts) > 1 else ""
            if name:
                self._new_project_data = {"name": name}
                self._new_project_step = 1
                self._post_system_message(
                    f"Starting project **{name}**.\n\n"
                    "**Step 1/3:** Describe the project (what it is and what it does):"
                )
            else:
                self._post_system_message("Usage: `/new-project <name>`")

        elif cmd == "/load-project":
            if len(parts) < 2:
                self._post_system_message("Usage: `/load-project <id>`")
                return
            try:
                pid = int(parts[1])
                ok = await state.set_active_project(pid)
                if ok:
                    self._post_system_message(f"Loaded project #{pid}")
                    asyncio.create_task(self._refresh_dashboard())
                else:
                    self._post_system_message(f"Project #{pid} not found.")
            except ValueError:
                self._post_system_message("Project ID must be a number.")

        elif cmd == "/projects":
            projects = await state.list_projects()
            if not projects:
                self._post_system_message("No projects found. Use `/new-project <name>`.")
            else:
                lines = ["**Projects:**\n"]
                for p in projects:
                    lines.append(f"  • **#{p.id}** {p.name}  [{p.status}]")
                self._post_system_message("\n".join(lines))

        elif cmd == "/status":
            asyncio.create_task(self._show_status())

        elif cmd == "/approve":
            if len(parts) < 2:
                # Approve first pending
                if self._pending_approvals:
                    dec_id = next(iter(self._pending_approvals))
                    asyncio.create_task(self._resolve_approval(dec_id, "Approved by user", True))
                else:
                    self._post_system_message("No pending approvals.")
            else:
                try:
                    dec_id = int(parts[1])
                    asyncio.create_task(self._resolve_approval(dec_id, "Approved by user", True))
                except ValueError:
                    self._post_system_message("Usage: `/approve <decision_id>`")

        elif cmd == "/reject":
            reason = parts[2] if len(parts) > 2 else "Rejected by user"
            if len(parts) < 2:
                self._post_system_message("Usage: `/reject <decision_id> [reason]`")
            else:
                try:
                    dec_id = int(parts[1])
                    asyncio.create_task(self._resolve_approval(dec_id, reason, False))
                except ValueError:
                    self._post_system_message("Usage: `/reject <decision_id>`")

        elif cmd == "/agents":
            from acp.protocol import router as acp_router
            from config import EXTERNAL_AGENTS
            all_agents = {**EXTERNAL_AGENTS, **acp_router.registered_agents}
            if all_agents:
                lines = ["**Registered External Agents:**\n"]
                for name, url in all_agents.items():
                    lines.append(f"  • **{name}**  →  `{url}`")
                self._post_system_message("\n".join(lines))
            else:
                self._post_system_message(
                    "No external agents registered.\n"
                    "Use `/acp-register <name> <url>` or set `OPENCLAW_URL` / `PAPERCLIP_URL` in `.env`."
                )

        elif cmd == "/acp-register":
            if len(parts) < 3:
                self._post_system_message("Usage: `/acp-register <name> <url>`")
            else:
                name, url = parts[1], parts[2]
                from acp.protocol import router as acp_router
                acp_router.register_external_agent(name, url)
                self._post_system_message(f"Registered external agent **{name}** at `{url}`")

        elif cmd == "/tasks":
            asyncio.create_task(self._show_tasks())

        elif cmd == "/strategies":
            asyncio.create_task(self._show_strategies())

        elif cmd == "/forecasts":
            asyncio.create_task(self._show_forecasts())

        elif cmd == "/logs":
            asyncio.create_task(self._show_logs())

        elif cmd == "/loop":
            sub = parts[1].lower() if len(parts) > 1 else "start"
            if sub == "start":
                if self._orchestrator:
                    self._orchestrator.start_autonomous_loop()
                    self._loop_running = True
                    self._post_system_message("Autonomous review loop **started**.")
                else:
                    self._post_system_message("No orchestrator available (check API key).")
            elif sub == "stop":
                if self._orchestrator:
                    self._orchestrator.stop_autonomous_loop()
                    self._loop_running = False
                    self._post_system_message("Autonomous review loop **stopped**.")

        else:
            self._post_system_message(f"Unknown command: `{cmd}`. Type `/help` for available commands.")

    async def _handle_chat(self, text: str) -> None:
        """Send a message to the orchestrator."""
        # Show user message
        await state.add_message(
            ChatMessage(sender="you", content=text, message_type="user")
        )

        if self._orchestrator is None:
            self._post_system_message("Orchestrator unavailable — check your ANTHROPIC_API_KEY in `.env`.")
            return

        # Run in background so UI stays responsive
        asyncio.create_task(self._run_agent_response(text))

    async def _run_agent_response(self, text: str) -> None:
        try:
            response = await self._orchestrator.handle_user_message(text)
            # Message is already saved by orchestrator via send_message tool
            # If it wasn't, save it now
            if response and response.strip():
                await state.add_message(
                    ChatMessage(sender="agent_team", content=response, message_type="chat")
                )
        except Exception as exc:
            self._post_system_message(f"Agent error: {exc}")

    # ─── New project wizard ──────────────────────────────────────────────────

    async def _handle_project_wizard(self, text: str) -> None:
        step = self._new_project_step

        if step == 1:
            self._new_project_data["description"] = text
            self._new_project_step = 2
            self._post_system_message(
                "**Step 2/3:** List the project goals (comma-separated, or one per line):"
            )

        elif step == 2:
            goals = [g.strip() for g in text.replace("\n", ",").split(",") if g.strip()]
            self._new_project_data["goals"] = goals
            self._new_project_step = 3
            self._post_system_message(
                "**Step 3/3:** Define the targets — key metrics and their values.\n"
                "Format: `metric: value, metric: value`  (or just press Enter to skip):"
            )

        elif step == 3:
            targets: dict = {}
            if text.strip():
                for part in text.split(","):
                    if ":" in part:
                        k, _, v = part.partition(":")
                        targets[k.strip()] = v.strip()
            self._new_project_data["targets"] = targets
            self._new_project_step = 0

            # Create project
            asyncio.create_task(self._create_project())

    async def _create_project(self) -> None:
        from core.models import ProjectCreate
        data = self._new_project_data

        self._post_system_message(f"Creating project **{data['name']}**...")

        project = await state.create_project(
            ProjectCreate(
                name=data["name"],
                description=data.get("description", ""),
                goals=data.get("goals", []),
                targets=data.get("targets", {}),
            )
        )

        self._post_system_message(
            f"Project **{project.name}** created (ID: {project.id}).\n\n"
            "Handing off to the agent team for initialisation..."
        )

        if self._orchestrator:
            asyncio.create_task(self._orchestrator.initialise_project(project.model_dump()))

        await self._refresh_dashboard()

    # ─── Dashboard refresh ────────────────────────────────────────────────────

    async def _refresh_dashboard(self) -> None:
        project = await state.get_active_project()
        tasks = await state.get_tasks()
        strategies = await state.get_strategies()
        forecasts = await state.get_forecasts()

        self.call_from_thread(
            self.query_one(ProjectMetricsWidget).update_data,
            project.model_dump() if project else None,
            [t.model_dump() for t in tasks],
            strategies,
            forecasts,
        ) if False else self.query_one(ProjectMetricsWidget).update_data(
            project.model_dump() if project else None,
            [t.model_dump() for t in tasks],
            strategies,
            forecasts,
        )

        self._update_header()

    # ─── Approval handling ────────────────────────────────────────────────────

    async def _resolve_approval(self, decision_id: int, response: str, approved: bool) -> None:
        await state.resolve_approval(decision_id, response, approved)
        self._post_system_message(
            f"Decision #{decision_id} **{'approved' if approved else 'rejected'}**."
        )

    # ─── Header ──────────────────────────────────────────────────────────────

    def _update_header(self) -> None:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self._async_update_header())

    async def _async_update_header(self) -> None:
        project = await state.get_active_project()
        name = project.name if project else "No Project Active"
        try:
            self.query_one("#header-project", Label).update(name)
        except Exception:
            pass

    # ─── Info commands ────────────────────────────────────────────────────────

    async def _show_status(self) -> None:
        project = await state.get_active_project()
        if project is None:
            self._post_system_message("No active project.")
            return
        tasks = await state.get_tasks()
        done = sum(1 for t in tasks if t.status == "done")
        lines = [
            f"## Project Status: {project.name}",
            f"**Status:** {project.status}",
            f"**ID:** {project.id}",
            f"",
            f"**Goals:**",
            *[f"  • {g}" for g in project.goals],
            f"",
            f"**Targets:**",
            *[f"  • {k}: {v}" for k, v in project.targets.items()],
            f"",
            f"**Tasks:** {done}/{len(tasks)} complete",
            f"**Pending approvals:** {len(self._pending_approvals)}",
        ]
        self._post_agent_message("\n".join(lines))

    async def _show_tasks(self) -> None:
        tasks = await state.get_tasks()
        if not tasks:
            self._post_system_message("No tasks yet.")
            return
        lines = ["**Tasks:**\n"]
        for t in tasks[:20]:
            lines.append(f"  • [{t.status.upper()}] **{t.agent_type}** — {t.title or t.description[:50]}")
        self._post_agent_message("\n".join(lines))

    async def _show_strategies(self) -> None:
        strategies = await state.get_strategies()
        if not strategies:
            self._post_system_message("No strategies saved yet.")
            return
        lines = ["**Strategies:**\n"]
        for s in strategies:
            lines.append(f"  • **{s['title']}** [{s.get('phase', '')}]")
        self._post_agent_message("\n".join(lines))

    async def _show_forecasts(self) -> None:
        forecasts = await state.get_forecasts()
        if not forecasts:
            self._post_system_message("No forecasts saved yet.")
            return
        lines = ["**Forecasts:**\n"]
        for f in forecasts[:10]:
            conf = int(f.get("confidence", 0) * 100)
            lines.append(f"  • **{f['period']}** — {f['metric']} ({conf}% confidence)")
            if f.get("narrative"):
                lines.append(f"    {f['narrative'][:80]}...")
        self._post_agent_message("\n".join(lines))

    async def _show_logs(self) -> None:
        logs = await state.get_recent_logs(limit=20)
        if not logs:
            self._post_system_message("No logs yet.")
            return
        lines = ["**Recent Agent Logs:**\n"]
        for log in logs[-15:]:
            ts = log["timestamp"][:19].replace("T", " ")
            lines.append(f"  `{ts}` **{log['agent']}** › {log['action']}")
        self._post_agent_message("\n".join(lines))

    # ─── State event callbacks ────────────────────────────────────────────────

    async def _on_message_added(self, event: str, data: Any) -> None:
        if isinstance(data, ChatMessage):
            self._add_chat_message(data)
        elif isinstance(data, dict):
            msg = ChatMessage(**data)
            self._add_chat_message(msg)

    def _add_chat_message(self, msg: ChatMessage) -> None:
        chat_log = self.query_one("#chat-log", ScrollableContainer)
        msg_type = msg.message_type
        if msg.sender == "you":
            msg_type = "user"
        elif msg.sender.startswith("external:"):
            msg_type = "external"
        widget = ChatMessageWidget(
            sender=msg.sender,
            content=msg.content,
            message_type=msg_type,
            timestamp=msg.timestamp,
        )
        chat_log.mount(widget)
        chat_log.scroll_end(animate=False)

    async def _on_agent_status_updated(self, event: str, data: Any) -> None:
        from core.models import AgentStatus
        if isinstance(data, dict):
            agent_type_val = data.get("agent_type")
            if agent_type_val:
                try:
                    agent_type = AgentType(agent_type_val)
                    status_obj = AgentStatus(**data)
                    widget = self._agent_widgets.get(agent_type)
                    if widget:
                        widget.update_status(status_obj)
                except Exception:
                    pass

    async def _on_approval_requested(self, event: str, data: Any) -> None:
        from core.models import DecisionInfo
        if isinstance(data, DecisionInfo):
            self._pending_approvals[data.id] = data
            self._add_approval_widget(data)

    def _add_approval_widget(self, decision: DecisionInfo) -> None:
        section = self.query_one("#approval-section", ScrollableContainer)
        widget = ApprovalWidget(
            decision=decision,
            on_approve=lambda did: asyncio.create_task(
                self._resolve_approval(did, "Approved via UI", True)
            ),
            on_reject=lambda did: asyncio.create_task(
                self._resolve_approval(did, "Rejected via UI", False)
            ),
        )
        section.mount(widget)

    async def _on_approval_resolved(self, event: str, data: Any) -> None:
        if isinstance(data, dict):
            dec_id = data.get("decision_id")
            if dec_id in self._pending_approvals:
                del self._pending_approvals[dec_id]
            # Remove widget
            for widget in self.query(f"#approval-section ApprovalWidget"):
                if hasattr(widget, "_decision") and widget._decision.id == dec_id:
                    await widget.remove()
                    break

    async def _on_project_changed(self, event: str, data: Any) -> None:
        asyncio.create_task(self._refresh_dashboard())

    async def _on_project_updated(self, event: str, data: Any) -> None:
        asyncio.create_task(self._refresh_dashboard())

    async def _on_dashboard_changed(self, event: str, data: Any) -> None:
        asyncio.create_task(self._refresh_dashboard())

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _post_system_message(self, content: str) -> None:
        msg = ChatMessage(sender="system", content=content, message_type="system")
        self._add_chat_message(msg)

    def _post_agent_message(self, content: str) -> None:
        msg = ChatMessage(sender="agent_team", content=content, message_type="chat")
        self._add_chat_message(msg)

    # ─── Key bindings ─────────────────────────────────────────────────────────

    def action_show_help(self) -> None:
        self._post_agent_message(HELP_TEXT)

    def action_toggle_loop(self) -> None:
        if self._orchestrator:
            if self._loop_running:
                self._orchestrator.stop_autonomous_loop()
                self._loop_running = False
                self._post_system_message("Autonomous loop **stopped** (F2).")
            else:
                self._orchestrator.start_autonomous_loop()
                self._loop_running = True
                self._post_system_message("Autonomous loop **started** (F2).")
