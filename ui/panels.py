"""
Custom Textual widgets used by the main app.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Label, RichLog, Static

from core.models import AgentStatus, AgentType, DecisionInfo


# ─── Agent status item ────────────────────────────────────────────────────────

STATUS_ICONS = {
    "idle": ("○", "status-idle"),
    "thinking": ("◐", "status-thinking"),
    "working": ("●", "status-working"),
    "waiting": ("◑", "status-waiting"),
    "error": ("✖", "status-error"),
}

AGENT_LABELS = {
    AgentType.ORCHESTRATOR: "Orchestrator",
    AgentType.STRATEGY: "Strategy",
    AgentType.RESEARCH: "Research",
    AgentType.IMPLEMENTATION: "Implementation",
    AgentType.FORECASTING: "Forecasting",
    AgentType.QA: "QA",
}


class AgentStatusWidget(Widget):
    """Displays one agent's status in the left panel."""

    status: reactive[AgentStatus] = reactive(None, recompose=True)

    def __init__(self, agent_type: AgentType, **kwargs: Any) -> None:
        super().__init__(**kwargs, classes="agent-item")
        self._agent_type = agent_type
        self.status = AgentStatus(agent_type=agent_type)

    def compose(self) -> ComposeResult:
        s = self.status
        icon, css_class = STATUS_ICONS.get(s.status if s else "idle", ("○", "status-idle"))
        label = AGENT_LABELS.get(self._agent_type, self._agent_type.value)
        yield Label(f"{icon}  {label}", classes=f"agent-name {css_class}")
        yield Label(s.status.upper() if s else "IDLE", classes=f"agent-status {css_class}")
        task = (s.current_task or "")[:28] if s else ""
        yield Label(task, classes="agent-task")

    def update_status(self, new_status: AgentStatus) -> None:
        self.status = new_status
        self.refresh(recompose=True)


# ─── Chat message widget ─────────────────────────────────────────────────────

class ChatMessageWidget(Widget):
    """A single chat bubble."""

    def __init__(self, sender: str, content: str, message_type: str, timestamp: datetime, **kw) -> None:
        css = {
            "user": "msg-user",
            "agent": "msg-agent",
            "system": "msg-system",
            "update": "msg-agent",
        }.get(message_type, "msg-agent")
        # External agents
        if sender.startswith("external:"):
            css = "msg-external"
        super().__init__(**kw, classes=css)
        self._sender = sender
        self._content = content
        self._timestamp = timestamp

    def compose(self) -> ComposeResult:
        ts = self._timestamp.strftime("%H:%M:%S")
        yield Label(self._sender, classes="msg-sender")
        yield Label(ts, classes="msg-time")
        yield Static(Markdown(self._content))


# ─── Approval widget ──────────────────────────────────────────────────────────

class ApprovalWidget(Widget):
    """A pending decision card with Approve / Reject buttons."""

    def __init__(self, decision: DecisionInfo, on_approve, on_reject, **kw) -> None:
        super().__init__(**kw, classes="approval-item")
        self._decision = decision
        self._on_approve = on_approve
        self._on_reject = on_reject

    def compose(self) -> ComposeResult:
        d = self._decision
        yield Label(f"[{d.urgency.upper()}] {d.title}", classes="approval-title")
        # Truncate description
        desc = d.description[:180] + ("..." if len(d.description) > 180 else "")
        yield Label(desc, classes="approval-desc")
        if d.recommendation:
            yield Label(f"Recommendation: {d.recommendation}", classes="approval-desc")
        if d.options:
            yield Label("Options: " + " | ".join(d.options), classes="approval-desc")
        with Horizontal(classes="approval-btns"):
            yield Button("Approve", id=f"approve-{d.id}", classes="approve-btn")
            yield Button("Reject", id=f"reject-{d.id}", classes="reject-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("approve-"):
            self._on_approve(self._decision.id)
        elif btn_id.startswith("reject-"):
            self._on_reject(self._decision.id)


# ─── Project metrics panel ───────────────────────────────────────────────────

class ProjectMetricsWidget(Widget):
    """Renders project stats inside the right panel."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw, id="project-dashboard")
        self._project: dict | None = None
        self._tasks: list[dict] = []
        self._strategies: list[dict] = []
        self._forecasts: list[dict] = []

    def compose(self) -> ComposeResult:
        if self._project is None:
            yield Label("No active project.", classes="metric-label")
            yield Label("Start with:  /new-project <name>", classes="metric-label")
            return

        p = self._project

        # Project header
        yield Label("PROJECT", classes="section-header")
        yield Label(p.get("name", "—"))
        yield Label(p.get("status", "active").upper(), classes="metric-value")

        # Goals
        goals = p.get("goals", [])
        yield Label(f"GOALS  ({len(goals)})", classes="section-header")
        for g in goals[:5]:
            yield Label(f"  • {g[:26]}", classes="metric-label")

        # Targets
        targets = p.get("targets", {})
        if targets:
            yield Label("TARGETS", classes="section-header")
            for k, v in list(targets.items())[:5]:
                with Horizontal(classes="metric-row"):
                    yield Label(f"  {k[:14]}", classes="metric-label")
                    yield Label(str(v)[:10], classes="metric-value")

        # Task summary
        if self._tasks:
            done = sum(1 for t in self._tasks if t.get("status") == "done")
            total = len(self._tasks)
            yield Label("TASKS", classes="section-header")
            with Horizontal(classes="metric-row"):
                yield Label("  Done", classes="metric-label")
                yield Label(f"{done}/{total}", classes="metric-value")

        # Latest strategies
        if self._strategies:
            yield Label(f"STRATEGIES  ({len(self._strategies)})", classes="section-header")
            for s in self._strategies[:3]:
                yield Label(f"  • {s.get('title', '')[:24]}", classes="metric-label")

        # Latest forecasts
        if self._forecasts:
            yield Label(f"FORECASTS  ({len(self._forecasts)})", classes="section-header")
            for f in self._forecasts[:3]:
                conf = int(f.get("confidence", 0) * 100)
                yield Label(
                    f"  {f.get('period','')}  {f.get('metric','')[:14]}  {conf}%",
                    classes="metric-label",
                )

    def update_data(
        self,
        project: dict | None,
        tasks: list[dict],
        strategies: list[dict],
        forecasts: list[dict],
    ) -> None:
        self._project = project
        self._tasks = tasks
        self._strategies = strategies
        self._forecasts = forecasts
        self.refresh(recompose=True)
