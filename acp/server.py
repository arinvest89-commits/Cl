"""
ACP FastAPI server — exposes HTTP endpoints so external agents can reach the team.

Endpoints:
  POST /acp/receive         — inbound messages from external agents
  GET  /acp/status          — health check + registered agents
  POST /acp/register        — register / update an external agent's endpoint
  DELETE /acp/register/{name} — deregister an external agent
  GET  /acp/agents          — list registered external agents
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from acp.protocol import router as acp_router
from config import ACP_HOST, ACP_PORT
from core.models import ACPMessage, ACPMessageType
from core.state import state


app = FastAPI(title="Agent Team ACP Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request bodies ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str
    endpoint: str


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.post("/acp/receive")
async def receive_message(message: ACPMessage) -> dict[str, Any]:
    """Primary inbound endpoint — all external agents POST here."""
    await state.log(
        "acp_server",
        f"received:{message.type.value}",
        f"from={message.sender} id={message.message_id}",
    )
    result = await acp_router.dispatch(message)
    return result


@app.get("/acp/status")
async def status() -> dict[str, Any]:
    project = await state.get_active_project()
    return {
        "ok": True,
        "timestamp": datetime.utcnow().isoformat(),
        "active_project": project.name if project else None,
        "registered_agents": acp_router.registered_agents,
        "agent_statuses": {k: v.model_dump() for k, v in state.get_agent_statuses().items()},
    }


@app.post("/acp/register")
async def register_agent(req: RegisterRequest) -> dict[str, Any]:
    acp_router.register_external_agent(req.name, req.endpoint)
    await state.log("acp_server", "agent_registered", f"name={req.name} endpoint={req.endpoint}")
    return {"ok": True, "name": req.name, "endpoint": req.endpoint}


@app.delete("/acp/register/{name}")
async def unregister_agent(name: str) -> dict[str, Any]:
    acp_router.unregister_external_agent(name)
    return {"ok": True, "name": name}


@app.get("/acp/agents")
async def list_agents() -> dict[str, Any]:
    return {
        "registered": acp_router.registered_agents,
        "team": [a.value for a in state.get_agent_statuses().keys()],
    }


# ─── Default task handler ─────────────────────────────────────────────────────

async def _handle_inbound_task(message: ACPMessage) -> dict[str, Any]:
    """Route an inbound TASK from an external agent to the orchestrator."""
    # Lazy import to avoid circular dependency
    from agents.orchestrator import OrchestratorAgent

    task_description = (
        f"[ACP TASK from {message.sender}]\n\n"
        f"Task type: {message.payload.get('task_type', 'unknown')}\n\n"
        f"Payload:\n{message.payload}"
    )

    await state.add_message(
        __import__("core.models", fromlist=["ChatMessage"]).ChatMessage(
            sender=f"external:{message.sender}",
            content=task_description,
            message_type="system",
        )
    )
    return {"queued": True, "task_description": task_description[:200]}


acp_router.on(ACPMessageType.TASK, _handle_inbound_task)


# ─── Runner ──────────────────────────────────────────────────────────────────

def run_server(host: str = ACP_HOST, port: int = ACP_PORT) -> None:
    """Start the ACP server (blocking)."""
    uvicorn.run(app, host=host, port=port, log_level="warning")


async def run_server_async(host: str = ACP_HOST, port: int = ACP_PORT) -> None:
    """Start the ACP server as an asyncio task (non-blocking)."""
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
