"""Persistent memory and state manager for the agent team."""
from __future__ import annotations
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from .models import Project, ChatMessage, AgentActivity, ApprovalRequest


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class MemoryManager:
    def __init__(self, base_dir: str = "data"):
        self.base = Path(base_dir)
        self.projects_dir = self.base / "projects"
        self.logs_dir = self.base / "logs"
        self.memory_dir = self.base / "memory"
        for d in [self.projects_dir, self.logs_dir, self.memory_dir]:
            d.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    # ── Project persistence ──────────────────────────────────────────────────

    async def save_project(self, project: Project) -> None:
        async with self._lock:
            path = self.projects_dir / f"{project.id}.json"
            project.updated_at = datetime.utcnow()
            path.write_text(
                json.dumps(json.loads(project.model_dump_json()), indent=2,
                           default=_json_default)
            )

    async def load_project(self, project_id: str) -> Optional[Project]:
        path = self.projects_dir / f"{project_id}.json"
        if not path.exists():
            return None
        return Project.model_validate_json(path.read_text())

    async def list_projects(self) -> list[Project]:
        projects = []
        for p in self.projects_dir.glob("*.json"):
            try:
                projects.append(Project.model_validate_json(p.read_text()))
            except Exception:
                pass
        return sorted(projects, key=lambda x: x.updated_at, reverse=True)

    async def delete_project(self, project_id: str) -> bool:
        path = self.projects_dir / f"{project_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # ── Chat history ────────────────────────────────────────────────────────

    async def save_chat_message(self, message: ChatMessage) -> None:
        path = self.logs_dir / "chat_history.jsonl"
        async with self._lock:
            with open(path, "a") as f:
                f.write(message.model_dump_json() + "\n")

    async def load_chat_history(self, limit: int = 100) -> list[ChatMessage]:
        path = self.logs_dir / "chat_history.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().strip().splitlines()
        messages = []
        for line in lines[-limit:]:
            try:
                messages.append(ChatMessage.model_validate_json(line))
            except Exception:
                pass
        return messages

    # ── Activity log ────────────────────────────────────────────────────────

    async def log_activity(self, activity: AgentActivity) -> None:
        path = self.logs_dir / "activity.jsonl"
        async with self._lock:
            with open(path, "a") as f:
                f.write(activity.model_dump_json() + "\n")

    async def load_activity_log(self, limit: int = 50) -> list[AgentActivity]:
        path = self.logs_dir / "activity.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().strip().splitlines()
        activities = []
        for line in lines[-limit:]:
            try:
                activities.append(AgentActivity.model_validate_json(line))
            except Exception:
                pass
        return activities

    # ── Approval requests ────────────────────────────────────────────────────

    async def save_approval(self, approval: ApprovalRequest) -> None:
        path = self.memory_dir / "approvals.jsonl"
        async with self._lock:
            existing = await self.load_approvals()
            updated = [a for a in existing if a.id != approval.id]
            updated.append(approval)
            with open(path, "w") as f:
                for a in updated:
                    f.write(a.model_dump_json() + "\n")

    async def load_approvals(self, pending_only: bool = False) -> list[ApprovalRequest]:
        path = self.memory_dir / "approvals.jsonl"
        if not path.exists():
            return []
        approvals = []
        for line in path.read_text().strip().splitlines():
            try:
                a = ApprovalRequest.model_validate_json(line)
                if not pending_only or a.status.value == "pending":
                    approvals.append(a)
            except Exception:
                pass
        return approvals

    # ── Agent memory (key-value store per agent) ────────────────────────────

    async def agent_remember(self, agent_role: str, key: str, value: Any) -> None:
        path = self.memory_dir / f"{agent_role}_memory.json"
        async with self._lock:
            data: dict = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                except Exception:
                    pass
            data[key] = value
            path.write_text(json.dumps(data, indent=2, default=_json_default))

    async def agent_recall(self, agent_role: str, key: str) -> Any:
        path = self.memory_dir / f"{agent_role}_memory.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return data.get(key)
        except Exception:
            return None

    async def agent_recall_all(self, agent_role: str) -> dict[str, Any]:
        path = self.memory_dir / f"{agent_role}_memory.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
