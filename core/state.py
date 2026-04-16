"""
ProjectStateManager — single source of truth for all in-memory + persisted
project state.  All agents read/write through this object.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Coroutine

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import APPROVAL_TIMEOUT
from core.database import (
    AgentLog,
    AsyncSessionLocal,
    Decision,
    Forecast,
    Message,
    Project,
    Strategy,
    Task,
)
from core.models import (
    AgentStatus,
    AgentType,
    ChatMessage,
    DecisionCreate,
    DecisionInfo,
    DecisionStatus,
    ForecastEntry,
    ProjectCreate,
    ProjectInfo,
    StrategyEntry,
    TaskCreate,
    TaskInfo,
    TaskStatus,
)


class ProjectStateManager:
    """Thread-safe async state manager for the active project."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_project_id: int | None = None

        # In-memory caches (kept consistent with DB)
        self._agent_statuses: dict[AgentType, AgentStatus] = {
            a: AgentStatus(agent_type=a) for a in AgentType
        }

        # Event subscribers: {event_name: [callback]}
        self._subscribers: dict[str, list[Callable]] = {}

        # Pending approval events keyed by decision_id
        self._approval_events: dict[int, asyncio.Event] = {}
        self._approval_responses: dict[int, str] = {}

    # ─── Project ─────────────────────────────────────────────────────────────

    async def create_project(self, data: ProjectCreate) -> ProjectInfo:
        async with AsyncSessionLocal() as session:
            project = Project(
                name=data.name,
                description=data.description,
                goals=data.goals,
                targets=data.targets,
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)
            self._active_project_id = project.id
            info = self._project_to_info(project)
        await self._emit("project_created", info)
        return info

    async def get_active_project(self) -> ProjectInfo | None:
        if self._active_project_id is None:
            return None
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Project).where(Project.id == self._active_project_id)
            )
            project = result.scalar_one_or_none()
            return self._project_to_info(project) if project else None

    async def update_project_field(self, field: str, value: Any) -> None:
        if self._active_project_id is None:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Project)
                .where(Project.id == self._active_project_id)
                .values(**{field: value, "updated_at": datetime.utcnow()})
            )
            await session.commit()
        await self._emit("project_updated", {"field": field, "value": value})

    async def list_projects(self) -> list[ProjectInfo]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Project).order_by(Project.created_at.desc()))
            return [self._project_to_info(p) for p in result.scalars()]

    async def set_active_project(self, project_id: int) -> bool:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Project).where(Project.id == project_id))
            project = result.scalar_one_or_none()
            if project is None:
                return False
            self._active_project_id = project_id
            await self._emit("project_activated", self._project_to_info(project))
            return True

    # ─── Tasks ───────────────────────────────────────────────────────────────

    async def create_task(self, data: TaskCreate) -> TaskInfo:
        async with AsyncSessionLocal() as session:
            task = Task(
                project_id=data.project_id,
                agent_type=data.agent_type.value,
                title=data.title,
                description=data.description,
                priority=data.priority.value,
                context=data.context,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            info = self._task_to_info(task)
        await self._emit("task_created", info)
        return info

    async def update_task(self, task_id: int, status: TaskStatus, result: str = "") -> None:
        async with AsyncSessionLocal() as session:
            vals: dict[str, Any] = {"status": status.value}
            if result:
                vals["result"] = result
            if status in (TaskStatus.DONE, TaskStatus.FAILED):
                vals["completed_at"] = datetime.utcnow()
            await session.execute(update(Task).where(Task.id == task_id).values(**vals))
            await session.commit()
        await self._emit("task_updated", {"task_id": task_id, "status": status})

    async def get_tasks(self, project_id: int | None = None) -> list[TaskInfo]:
        pid = project_id or self._active_project_id
        if pid is None:
            return []
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Task).where(Task.project_id == pid).order_by(Task.created_at.desc())
            )
            return [self._task_to_info(t) for t in result.scalars()]

    # ─── Decisions / Approvals ───────────────────────────────────────────────

    async def request_approval(self, data: DecisionCreate) -> tuple[int, asyncio.Event]:
        async with AsyncSessionLocal() as session:
            dec = Decision(
                project_id=data.project_id,
                title=data.title,
                description=data.description,
                options=data.options,
                recommendation=data.recommendation,
                urgency=data.urgency,
            )
            session.add(dec)
            await session.commit()
            await session.refresh(dec)
            decision_id = dec.id
            info = self._decision_to_info(dec)

        event = asyncio.Event()
        self._approval_events[decision_id] = event
        await self._emit("approval_requested", info)
        return decision_id, event

    async def resolve_approval(self, decision_id: int, response: str, approved: bool) -> None:
        status = DecisionStatus.APPROVED if approved else DecisionStatus.REJECTED
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Decision)
                .where(Decision.id == decision_id)
                .values(
                    status=status.value,
                    user_response=response,
                    resolved_at=datetime.utcnow(),
                )
            )
            await session.commit()
        self._approval_responses[decision_id] = response
        if decision_id in self._approval_events:
            self._approval_events[decision_id].set()
        await self._emit("approval_resolved", {"decision_id": decision_id, "approved": approved})

    async def get_pending_approvals(self) -> list[DecisionInfo]:
        if self._active_project_id is None:
            return []
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Decision).where(
                    Decision.project_id == self._active_project_id,
                    Decision.status == "pending",
                )
            )
            return [self._decision_to_info(d) for d in result.scalars()]

    # ─── Messages ────────────────────────────────────────────────────────────

    async def add_message(self, msg: ChatMessage) -> None:
        async with AsyncSessionLocal() as session:
            m = Message(
                project_id=msg.project_id or self._active_project_id,
                sender=msg.sender,
                content=msg.content,
                message_type=msg.message_type,
            )
            session.add(m)
            await session.commit()
        await self._emit("message_added", msg)

    async def get_messages(self, limit: int = 100) -> list[ChatMessage]:
        if self._active_project_id is None:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Message).order_by(Message.timestamp.desc()).limit(limit)
                )
                msgs = result.scalars().all()
        else:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Message)
                    .where(Message.project_id == self._active_project_id)
                    .order_by(Message.timestamp.desc())
                    .limit(limit)
                )
                msgs = result.scalars().all()
        return [
            ChatMessage(
                sender=m.sender,
                content=m.content,
                message_type=m.message_type,
                project_id=m.project_id,
                timestamp=m.timestamp,
            )
            for m in reversed(msgs)
        ]

    # ─── Logs ────────────────────────────────────────────────────────────────

    async def log(
        self,
        agent_type: str,
        action: str,
        details: str = "",
        level: str = "info",
    ) -> None:
        async with AsyncSessionLocal() as session:
            entry = AgentLog(
                project_id=self._active_project_id,
                agent_type=agent_type,
                action=action,
                details=details,
                level=level,
            )
            session.add(entry)
            await session.commit()
        await self._emit(
            "agent_log",
            {"agent": agent_type, "action": action, "details": details, "level": level},
        )

    async def get_recent_logs(self, limit: int = 200) -> list[dict]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AgentLog).order_by(AgentLog.timestamp.desc()).limit(limit)
            )
            return [
                {
                    "agent": l.agent_type,
                    "action": l.action,
                    "details": l.details,
                    "level": l.level,
                    "timestamp": l.timestamp.isoformat(),
                }
                for l in reversed(result.scalars().all())
            ]

    # ─── Forecasts ───────────────────────────────────────────────────────────

    async def save_forecast(self, project_id: int, entry: ForecastEntry) -> None:
        async with AsyncSessionLocal() as session:
            f = Forecast(
                project_id=project_id,
                period=entry.period,
                metric=entry.metric,
                value=entry.value,
                narrative=entry.narrative,
                confidence=entry.confidence,
            )
            session.add(f)
            await session.commit()
        await self._emit("forecast_saved", entry.model_dump())

    async def get_forecasts(self, project_id: int | None = None) -> list[dict]:
        pid = project_id or self._active_project_id
        if pid is None:
            return []
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Forecast)
                .where(Forecast.project_id == pid)
                .order_by(Forecast.created_at.desc())
            )
            return [
                {
                    "period": f.period,
                    "metric": f.metric,
                    "value": f.value,
                    "narrative": f.narrative,
                    "confidence": f.confidence,
                }
                for f in result.scalars()
            ]

    # ─── Strategies ──────────────────────────────────────────────────────────

    async def save_strategy(self, project_id: int, entry: StrategyEntry) -> None:
        async with AsyncSessionLocal() as session:
            s = Strategy(
                project_id=project_id,
                title=entry.title,
                content=entry.content,
                phase=entry.phase,
                priority=entry.priority,
            )
            session.add(s)
            await session.commit()
        await self._emit("strategy_saved", entry.model_dump())

    async def get_strategies(self, project_id: int | None = None) -> list[dict]:
        pid = project_id or self._active_project_id
        if pid is None:
            return []
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Strategy)
                .where(Strategy.project_id == pid)
                .order_by(Strategy.priority.desc())
            )
            return [
                {"title": s.title, "content": s.content, "phase": s.phase, "status": s.status}
                for s in result.scalars()
            ]

    # ─── Agent Status ─────────────────────────────────────────────────────────

    def update_agent_status(
        self,
        agent_type: AgentType,
        status: str,
        current_task: str | None = None,
        last_action: str | None = None,
    ) -> None:
        s = self._agent_statuses[agent_type]
        s.status = status
        if current_task is not None:
            s.current_task = current_task
        if last_action is not None:
            s.last_action = last_action
        s.last_updated = datetime.utcnow()
        # Fire-and-forget emit (non-async context)
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self._emit("agent_status_updated", s.model_dump()))

    def get_agent_statuses(self) -> dict[str, AgentStatus]:
        return {k.value: v for k, v in self._agent_statuses.items()}

    # ─── Pub/Sub ─────────────────────────────────────────────────────────────

    def subscribe(self, event: str, callback: Callable) -> None:
        self._subscribers.setdefault(event, []).append(callback)

    def unsubscribe(self, event: str, callback: Callable) -> None:
        if event in self._subscribers:
            self._subscribers[event] = [c for c in self._subscribers[event] if c != callback]

    async def _emit(self, event: str, data: Any) -> None:
        for cb in self._subscribers.get(event, []) + self._subscribers.get("*", []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event, data)
                else:
                    cb(event, data)
            except Exception:
                pass

    # ─── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _project_to_info(p: Project) -> ProjectInfo:
        return ProjectInfo(
            id=p.id,
            name=p.name,
            description=p.description or "",
            goals=p.goals or [],
            targets=p.targets or {},
            status=p.status or "active",
            metadata_=p.metadata_ or {},
            created_at=p.created_at or datetime.utcnow(),
            updated_at=p.updated_at or datetime.utcnow(),
        )

    @staticmethod
    def _task_to_info(t: Task) -> TaskInfo:
        return TaskInfo(
            id=t.id,
            project_id=t.project_id,
            agent_type=t.agent_type,
            title=t.title or "",
            description=t.description or "",
            priority=t.priority or "medium",
            status=t.status or "pending",
            result=t.result or "",
            created_at=t.created_at or datetime.utcnow(),
        )

    @staticmethod
    def _decision_to_info(d: Decision) -> DecisionInfo:
        return DecisionInfo(
            id=d.id,
            project_id=d.project_id,
            title=d.title,
            description=d.description or "",
            options=d.options or [],
            recommendation=d.recommendation or "",
            urgency=d.urgency or "normal",
            status=d.status or "pending",
            user_response=d.user_response,
            created_at=d.created_at or datetime.utcnow(),
        )

    @property
    def active_project_id(self) -> int | None:
        return self._active_project_id


# ─── Singleton ────────────────────────────────────────────────────────────────
state = ProjectStateManager()
