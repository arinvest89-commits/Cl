"""Pydantic schemas used across the system for type-safe data exchange."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────────────

class AgentType(str, Enum):
    ORCHESTRATOR = "orchestrator"
    STRATEGY = "strategy"
    RESEARCH = "research"
    IMPLEMENTATION = "implementation"
    FORECASTING = "forecasting"
    QA = "qa"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class DecisionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ─── Project ─────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    goals: list[str] = Field(default_factory=list)
    targets: dict[str, Any] = Field(default_factory=dict)


class ProjectInfo(BaseModel):
    id: int
    name: str
    description: str
    goals: list[str]
    targets: dict[str, Any]
    status: str
    metadata_: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── Task ────────────────────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    project_id: int
    agent_type: AgentType
    title: str = ""
    description: str
    priority: Priority = Priority.MEDIUM
    context: dict[str, Any] = Field(default_factory=dict)


class TaskInfo(BaseModel):
    id: int
    project_id: int
    agent_type: str
    title: str
    description: str
    priority: str
    status: str
    result: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Decision ────────────────────────────────────────────────────────────────

class DecisionCreate(BaseModel):
    project_id: int
    title: str
    description: str
    options: list[str] = Field(default_factory=list)
    recommendation: str = ""
    urgency: str = "normal"


class DecisionInfo(BaseModel):
    id: int
    project_id: int
    title: str
    description: str
    options: list[str]
    recommendation: str
    urgency: str
    status: str
    user_response: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Messages ────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    sender: str
    content: str
    message_type: str = "chat"
    project_id: int | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ─── Forecasts ───────────────────────────────────────────────────────────────

class ForecastEntry(BaseModel):
    period: str
    metric: str
    value: float | None
    narrative: str
    confidence: float = 0.7


# ─── Strategies ──────────────────────────────────────────────────────────────

class StrategyEntry(BaseModel):
    title: str
    content: str
    phase: str = "active"
    priority: int = 5


# ─── ACP Messages ────────────────────────────────────────────────────────────

class ACPMessageType(str, Enum):
    TASK = "TASK"
    RESULT = "RESULT"
    STATUS = "STATUS"
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    ERROR = "ERROR"
    HEARTBEAT = "HEARTBEAT"


class ACPMessage(BaseModel):
    message_id: str
    type: ACPMessageType
    sender: str                          # agent name / system name
    recipient: str                       # target agent name or "broadcast"
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None    # links replies to requests
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ─── Agent Status ─────────────────────────────────────────────────────────────

class AgentStatus(BaseModel):
    agent_type: AgentType
    status: str = "idle"           # idle / thinking / working / waiting
    current_task: str | None = None
    last_action: str | None = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)
