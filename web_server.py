"""
Web server for the Agent Team UI.
FastAPI + WebSockets — serves the dashboard and handles all agent interactions.
Run: python web_server.py
Access: http://localhost:7860
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from config import CONFIG
from core.memory import MemoryManager
from core.orchestrator import Orchestrator
from core.acp import ACPManager
from core.approval import ApprovalManager
from core.models import (
    Project, ProjectStatus, TaskStatus, ApprovalRequest,
    ChatMessage, MessageRole, AgentRole, AgentActivity,
)

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Agent Team", docs_url=None, redoc_url=None)

memory = MemoryManager()
orchestrator = Orchestrator(memory, CONFIG["agent_team"]["model"])
approval_manager = ApprovalManager(memory, auto_approve_threshold="low")
acp_cfg = CONFIG.get("acp", {})
acp = ACPManager(
    host=acp_cfg.get("host", "0.0.0.0"),
    port=acp_cfg.get("port", 8765),
    known_agents=acp_cfg.get("known_agents", {}),
    message_handler=orchestrator.handle_acp_message,
)

# Active WebSocket connections
_ws_clients: list[WebSocket] = []


# ── WebSocket broadcast ────────────────────────────────────────────────────────

async def broadcast(event: str, data: Any) -> None:
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json({"event": event, "data": data, "ts": datetime.utcnow().isoformat()})
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


def _msg_to_dict(msg: ChatMessage) -> dict:
    return {
        "id": msg.id,
        "role": msg.role.value,
        "agent": msg.agent.value if msg.agent else None,
        "content": msg.content,
        "project_id": msg.project_id,
        "timestamp": msg.timestamp.isoformat(),
    }


def _activity_to_dict(a: AgentActivity) -> dict:
    return {
        "agent": a.agent.value,
        "action": a.action,
        "description": a.description,
        "success": a.success,
        "timestamp": a.timestamp.isoformat(),
    }


def _approval_to_dict(a: ApprovalRequest) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "description": a.description,
        "options": a.options,
        "recommended_option": a.recommended_option,
        "impact_level": a.impact_level,
        "status": a.status.value,
        "decision_type": a.decision_type,
    }


def _project_to_dict(p: Project) -> dict:
    p.compute_metrics()
    m = p.metrics
    forecast_info = None
    if p.forecast and p.forecast.predicted_completion:
        days = (p.forecast.predicted_completion - datetime.utcnow()).days
        forecast_info = {
            "days": days,
            "confidence": p.forecast.confidence,
            "risks": len(p.forecast.risks),
            "recommendations": p.forecast.recommendations[:3],
        }
    return {
        "id": p.id,
        "name": p.name,
        "outline": p.outline,
        "goals": p.goals,
        "targets": p.targets,
        "status": p.status.value,
        "metrics": {
            "completion_rate": round(m.completion_rate, 1),
            "health_score": round(m.health_score, 1),
            "tasks_total": m.tasks_total,
            "tasks_completed": m.tasks_completed,
            "tasks_in_progress": m.tasks_in_progress,
            "tasks_blocked": m.tasks_blocked,
        },
        "strategies": len(p.strategies),
        "integrations": len(p.integrations),
        "milestones": len(p.milestones),
        "tasks": [
            {
                "id": t.id, "title": t.title, "status": t.status.value,
                "priority": t.priority.value,
                "assigned_to": t.assigned_to.value if t.assigned_to else None,
                "description": t.description[:120],
            }
            for t in p.tasks[:50]
        ],
        "forecast": forecast_info,
        "updated_at": p.updated_at.isoformat(),
    }


# ── Orchestrator callbacks ─────────────────────────────────────────────────────

def _on_message(msg: ChatMessage) -> None:
    asyncio.create_task(broadcast("message", _msg_to_dict(msg)))


def _on_activity(a: AgentActivity) -> None:
    asyncio.create_task(broadcast("activity", _activity_to_dict(a)))


def _on_approval(a: ApprovalRequest) -> None:
    asyncio.create_task(broadcast("approval", _approval_to_dict(a)))


orchestrator.on_message(_on_message)
orchestrator.on_activity(_on_activity)
orchestrator.on_approval_needed(_on_approval)


# ── Startup / shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    await acp.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await acp.stop()


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/projects")
async def list_projects():
    projects = await memory.list_projects()
    return [_project_to_dict(p) for p in projects]


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    p = await memory.load_project(project_id)
    if not p:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return _project_to_dict(p)


@app.post("/api/projects")
async def create_project(body: dict):
    name = body.get("name", "").strip()
    outline = body.get("outline", "").strip()
    goals = body.get("goals", [])
    targets = body.get("targets", [])
    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)
    project = await orchestrator.initialise_project(name, outline, goals, targets)
    return _project_to_dict(project)


@app.post("/api/projects/{project_id}/daily")
async def daily_cycle(project_id: str):
    asyncio.create_task(orchestrator.run_daily_cycle(project_id))
    return {"status": "Daily cycle started"}


@app.get("/api/projects/{project_id}/status")
async def project_status(project_id: str):
    p = await memory.load_project(project_id)
    if not p:
        return JSONResponse({"error": "Not found"}, status_code=404)
    summary = await orchestrator.synthesise_status(p)
    return {"summary": summary, "project": _project_to_dict(p)}


@app.get("/api/chat/history")
async def chat_history(limit: int = 80):
    messages = await memory.load_chat_history(limit=limit)
    return [_msg_to_dict(m) for m in messages]


@app.post("/api/chat")
async def chat(body: dict):
    message = body.get("message", "").strip()
    project_id = body.get("project_id")
    if not message:
        return JSONResponse({"error": "Message required"}, status_code=400)
    response = await orchestrator.chat(message, project_id)
    return {"response": response}


@app.get("/api/activity")
async def activity_log(limit: int = 50):
    activities = await memory.load_activity_log(limit=limit)
    return [_activity_to_dict(a) for a in activities]


@app.get("/api/approvals")
async def get_approvals(pending_only: bool = False):
    approvals = await memory.load_approvals(pending_only=pending_only)
    return [_approval_to_dict(a) for a in approvals]


@app.post("/api/approvals/{approval_id}/resolve")
async def resolve_approval(approval_id: str, body: dict):
    choice = body.get("choice", "")
    notes = body.get("notes", "")
    result = await orchestrator.resolve_approval(approval_id, choice, notes)
    return {"result": result}


@app.get("/api/agents/status")
async def agents_status():
    return acp.agent_statuses()


@app.post("/api/agents/ping")
async def ping_agents():
    results = await acp.discover_agents()
    return results


@app.post("/api/action")
async def run_action(body: dict):
    action = body.get("action", "")
    project_id = body.get("project_id")
    directive = body.get("directive", "")
    p = await memory.load_project(project_id) if project_id else None

    result = ""
    if not p:
        return JSONResponse({"error": "No active project"}, status_code=400)

    if action == "research":
        result = await orchestrator.researcher.handle_directive(directive or "Full market scan and performance analysis", p)
        await broadcast("message", _msg_to_dict(ChatMessage(role=MessageRole.AGENT, agent=AgentRole.RESEARCHER, content=result, project_id=project_id)))
    elif action == "strategy":
        result = await orchestrator.strategist.handle_directive(directive or "Review and update strategy", p)
        await broadcast("message", _msg_to_dict(ChatMessage(role=MessageRole.AGENT, agent=AgentRole.STRATEGIST, content=result, project_id=project_id)))
    elif action == "forecast":
        await orchestrator.forecaster.generate_forecast(p)
        result = await orchestrator.forecaster.handle_directive("Full project forecast", p)
        await memory.save_project(p)
        await broadcast("project_update", _project_to_dict(p))
        await broadcast("message", _msg_to_dict(ChatMessage(role=MessageRole.AGENT, agent=AgentRole.FORECASTER, content=result, project_id=project_id)))
    elif action == "test":
        result = await orchestrator.qa.create_test_plan(p)
        await broadcast("message", _msg_to_dict(ChatMessage(role=MessageRole.AGENT, agent=AgentRole.QA, content=result, project_id=project_id)))
    elif action == "tools":
        tools = await orchestrator.integrator.discover_tools(p)
        result = json.dumps(tools, indent=2)
        return {"result": result, "tools": tools}
    elif action == "standup":
        result = await orchestrator.operations.daily_standup(p)
        await broadcast("message", _msg_to_dict(ChatMessage(role=MessageRole.AGENT, agent=AgentRole.OPERATIONS, content=result, project_id=project_id)))
    elif action == "risk":
        result = await orchestrator.forecaster.risk_assessment(p)
        await broadcast("message", _msg_to_dict(ChatMessage(role=MessageRole.AGENT, agent=AgentRole.FORECASTER, content=result, project_id=project_id)))

    await memory.save_project(p)
    return {"result": result}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.append(websocket)
    # Send initial state
    projects = await memory.list_projects()
    messages = await memory.load_chat_history(limit=60)
    activities = await memory.load_activity_log(limit=40)
    approvals = await memory.load_approvals(pending_only=True)
    await websocket.send_json({
        "event": "init",
        "data": {
            "projects": [_project_to_dict(p) for p in projects],
            "messages": [_msg_to_dict(m) for m in messages],
            "activities": [_activity_to_dict(a) for a in activities],
            "approvals": [_approval_to_dict(a) for a in approvals],
            "agent_statuses": acp.agent_statuses(),
        }
    })
    try:
        while True:
            data = await websocket.receive_text()
            # Handle pings
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        _ws_clients.remove(websocket) if websocket in _ws_clients else None


# ── Main HTML UI ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(Path(__file__).parent / "ui" / "web.html") as f:
        return HTMLResponse(f.read())


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.")
        sys.exit(1)
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
