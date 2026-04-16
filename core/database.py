"""SQLAlchemy async ORM — all persistent tables live here."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship

from config import DATABASE_URL


class Base(DeclarativeBase):
    pass


# ─── Tables ──────────────────────────────────────────────────────────────────

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, default="")
    goals = Column(JSON, default=list)          # list[str]
    targets = Column(JSON, default=dict)        # {metric: value}
    status = Column(String(64), default="active")
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="project", cascade="all, delete-orphan")
    decisions = relationship("Decision", back_populates="project", cascade="all, delete-orphan")
    logs = relationship("AgentLog", back_populates="project", cascade="all, delete-orphan")
    forecasts = relationship("Forecast", back_populates="project", cascade="all, delete-orphan")
    strategies = relationship("Strategy", back_populates="project", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    agent_type = Column(String(64), nullable=False)
    title = Column(String(256), default="")
    description = Column(Text, default="")
    priority = Column(String(32), default="medium")
    status = Column(String(64), default="pending")   # pending / running / done / failed
    result = Column(Text, default="")
    context = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="tasks")


class Decision(Base):
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    title = Column(String(256), nullable=False)
    description = Column(Text, default="")
    options = Column(JSON, default=list)           # list[str]
    recommendation = Column(Text, default="")
    urgency = Column(String(32), default="normal")
    status = Column(String(32), default="pending") # pending / approved / rejected / expired
    user_response = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="decisions")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    sender = Column(String(128), nullable=False)    # "user" | agent name
    content = Column(Text, nullable=False)
    message_type = Column(String(64), default="chat")  # chat / system / approval
    timestamp = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="messages")


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    agent_type = Column(String(64), nullable=False)
    action = Column(String(256), nullable=False)
    details = Column(Text, default="")
    level = Column(String(32), default="info")   # info / warning / error
    timestamp = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="logs")


class Forecast(Base):
    __tablename__ = "forecasts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    period = Column(String(64), nullable=False)    # "week-1", "month-3", etc.
    metric = Column(String(128), nullable=False)
    value = Column(Float, nullable=True)
    narrative = Column(Text, default="")
    confidence = Column(Float, default=0.7)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="forecasts")


class Strategy(Base):
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    title = Column(String(256), nullable=False)
    content = Column(Text, default="")
    phase = Column(String(64), default="active")
    priority = Column(Integer, default=5)
    status = Column(String(64), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="strategies")


# ─── Engine + Session factory ─────────────────────────────────────────────────

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create all tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:  # noqa: D103
    async with AsyncSessionLocal() as session:
        yield session
