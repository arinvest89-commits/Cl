from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
import uuid


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ProjectStatus(str, Enum):
    PLANNING = "planning"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"


class MessageRole(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    RESEARCHER = "researcher"
    STRATEGIST = "strategist"
    OPERATIONS = "operations"
    FORECASTER = "forecaster"
    QA = "qa"
    INTEGRATOR = "integrator"
    EXTERNAL = "external"


class ProjectTask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str
    description: str
    assigned_to: Optional[AgentRole] = None
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    due_date: Optional[datetime] = None
    dependencies: list[str] = Field(default_factory=list)
    results: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Milestone(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str
    description: str
    target_date: Optional[datetime] = None
    status: TaskStatus = TaskStatus.PENDING
    success_criteria: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)  # task IDs


class ProjectMetrics(BaseModel):
    tasks_total: int = 0
    tasks_completed: int = 0
    tasks_in_progress: int = 0
    tasks_blocked: int = 0
    completion_rate: float = 0.0
    velocity: float = 0.0  # tasks per day
    health_score: float = 100.0  # 0-100
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class ForecastData(BaseModel):
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    horizon_days: int = 30
    predicted_completion: Optional[datetime] = None
    confidence: float = 0.0
    risks: list[dict[str, Any]] = Field(default_factory=list)
    opportunities: list[dict[str, Any]] = Field(default_factory=list)
    resource_needs: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class Project(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str
    outline: str
    goals: list[str]
    targets: list[str]
    status: ProjectStatus = ProjectStatus.PLANNING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    tasks: list[ProjectTask] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    metrics: ProjectMetrics = Field(default_factory=ProjectMetrics)
    strategies: list[dict[str, Any]] = Field(default_factory=list)
    integrations: list[dict[str, Any]] = Field(default_factory=list)
    forecast: Optional[ForecastData] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def get_task(self, task_id: str) -> Optional[ProjectTask]:
        return next((t for t in self.tasks if t.id == task_id), None)

    def compute_metrics(self) -> ProjectMetrics:
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)
        in_progress = sum(1 for t in self.tasks if t.status == TaskStatus.IN_PROGRESS)
        blocked = sum(1 for t in self.tasks if t.status == TaskStatus.BLOCKED)
        rate = (completed / total * 100) if total > 0 else 0.0
        health = max(0.0, 100.0 - (blocked * 10))
        self.metrics = ProjectMetrics(
            tasks_total=total,
            tasks_completed=completed,
            tasks_in_progress=in_progress,
            tasks_blocked=blocked,
            completion_rate=rate,
            health_score=health,
        )
        return self.metrics


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    project_id: str
    requesting_agent: AgentRole
    decision_type: str
    title: str
    description: str
    options: list[str] = Field(default_factory=list)
    recommended_option: Optional[str] = None
    impact_level: str = "medium"  # low/medium/high
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    chosen_option: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    role: MessageRole
    agent: Optional[AgentRole] = None
    content: str
    project_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentActivity(BaseModel):
    agent: AgentRole
    action: str
    description: str
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    success: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ACPMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender: str
    recipient: str
    message_type: str  # request/response/broadcast/event
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    correlation_id: Optional[str] = None
    requires_response: bool = False
