"""
Concrete tool implementations that agents call via Claude's tool_use.
Each function maps to an Anthropic tool definition returned by `get_tool_schemas()`.
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
from datetime import datetime
from typing import Any

import httpx

from config import EXTERNAL_AGENTS
from core.models import (
    AgentType,
    DecisionCreate,
    ForecastEntry,
    Priority,
    StrategyEntry,
    TaskCreate,
    TaskStatus,
)
from core.state import state


# ─── Tool schema builder ──────────────────────────────────────────────────────

def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


# ─── Shared tool schemas ──────────────────────────────────────────────────────

SHARED_TOOLS = [
    _tool(
        "get_project_state",
        "Retrieve the full current state of the active project including goals, targets, "
        "recent tasks, strategies, and forecasts.",
        {},
        [],
    ),
    _tool(
        "log_activity",
        "Record an activity or observation to the project log.",
        {
            "action": {"type": "string", "description": "Short action label"},
            "details": {"type": "string", "description": "Full details"},
            "level": {
                "type": "string",
                "enum": ["info", "warning", "error"],
                "description": "Log level",
            },
        },
        ["action"],
    ),
    _tool(
        "send_message",
        "Send a message to the user via the chat interface.",
        {
            "content": {"type": "string", "description": "Message text (markdown supported)"},
            "message_type": {
                "type": "string",
                "enum": ["chat", "system", "update"],
                "description": "Message category",
            },
        },
        ["content"],
    ),
    _tool(
        "web_search",
        "Search the web for current information, market data, or research. "
        "Returns a summary of top results.",
        {
            "query": {"type": "string", "description": "Search query"},
            "focus": {
                "type": "string",
                "description": "Optional focus area (e.g. 'market trends', 'competitors')",
            },
        },
        ["query"],
    ),
]

# ─── Orchestrator-specific tools ──────────────────────────────────────────────

ORCHESTRATOR_TOOLS = SHARED_TOOLS + [
    _tool(
        "create_task",
        "Create a task and assign it to a specialized agent for async execution.",
        {
            "agent_type": {
                "type": "string",
                "enum": ["strategy", "research", "implementation", "forecasting", "qa"],
            },
            "title": {"type": "string"},
            "description": {"type": "string", "description": "Full task description"},
            "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "context": {
                "type": "object",
                "description": "Extra context passed to the agent",
            },
        },
        ["agent_type", "title", "description"],
    ),
    _tool(
        "request_approval",
        "Surface a key decision to the user and wait for approval before proceeding. "
        "Use for significant changes, resource commitments, or irreversible actions.",
        {
            "title": {"type": "string", "description": "Short decision title"},
            "description": {"type": "string", "description": "Full context and implications"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Available choices",
            },
            "recommendation": {"type": "string", "description": "Agent's recommended option"},
            "urgency": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
        },
        ["title", "description"],
    ),
    _tool(
        "update_project_field",
        "Update a specific field of the active project.",
        {
            "field": {
                "type": "string",
                "enum": ["status", "metadata_"],
                "description": "Field to update",
            },
            "value": {"description": "New value"},
        },
        ["field", "value"],
    ),
    _tool(
        "communicate_with_external_agent",
        "Send a task or message to an external agent (openclaw, paperclip) via ACP.",
        {
            "agent_name": {
                "type": "string",
                "enum": list(EXTERNAL_AGENTS.keys()) or ["openclaw", "paperclip"],
            },
            "task_type": {"type": "string", "description": "Type of task"},
            "payload": {"type": "object", "description": "Task payload"},
        },
        ["agent_name", "task_type", "payload"],
    ),
]

# ─── Strategy-specific tools ──────────────────────────────────────────────────

STRATEGY_TOOLS = SHARED_TOOLS + [
    _tool(
        "save_strategy",
        "Persist a strategy plan to the project.",
        {
            "title": {"type": "string"},
            "content": {"type": "string", "description": "Full strategy document (markdown)"},
            "phase": {"type": "string", "description": "Phase label e.g. 'phase-1'"},
            "priority": {"type": "integer", "description": "1-10, higher = more important"},
        },
        ["title", "content"],
    ),
    _tool(
        "get_strategies",
        "Retrieve all saved strategies for the active project.",
        {},
        [],
    ),
]

# ─── Research-specific tools ──────────────────────────────────────────────────

RESEARCH_TOOLS = SHARED_TOOLS + [
    _tool(
        "fetch_url",
        "Fetch and extract the text content of a specific URL.",
        {
            "url": {"type": "string"},
            "extract": {
                "type": "string",
                "description": "What specifically to look for on the page",
            },
        },
        ["url"],
    ),
]

# ─── Implementation-specific tools ───────────────────────────────────────────

IMPLEMENTATION_TOOLS = SHARED_TOOLS + [
    _tool(
        "record_implementation",
        "Record a completed implementation action.",
        {
            "action_title": {"type": "string"},
            "description": {"type": "string"},
            "outcome": {"type": "string"},
            "next_steps": {"type": "array", "items": {"type": "string"}},
        },
        ["action_title", "description", "outcome"],
    ),
    _tool(
        "identify_tool_or_resource",
        "Identify and record a tool or resource that would benefit the project.",
        {
            "name": {"type": "string"},
            "category": {"type": "string", "description": "e.g. analytics, automation, comms"},
            "rationale": {"type": "string"},
            "integration_steps": {"type": "string"},
            "estimated_impact": {"type": "string"},
        },
        ["name", "category", "rationale"],
    ),
]

# ─── Forecasting-specific tools ───────────────────────────────────────────────

FORECASTING_TOOLS = SHARED_TOOLS + [
    _tool(
        "save_forecast",
        "Save a forecast entry for a specific metric and period.",
        {
            "period": {"type": "string", "description": "e.g. 'week-1', 'month-3', 'quarter-2'"},
            "metric": {"type": "string", "description": "The metric being forecasted"},
            "value": {"type": "number", "description": "Forecasted numeric value (if applicable)"},
            "narrative": {"type": "string", "description": "Written forecast explanation"},
            "confidence": {
                "type": "number",
                "description": "Confidence 0.0-1.0",
            },
        },
        ["period", "metric", "narrative"],
    ),
    _tool(
        "get_forecasts",
        "Retrieve all previously saved forecasts.",
        {},
        [],
    ),
]

# ─── QA-specific tools ───────────────────────────────────────────────────────

QA_TOOLS = SHARED_TOOLS + [
    _tool(
        "record_qa_finding",
        "Record a QA finding, test result, or issue discovered.",
        {
            "category": {
                "type": "string",
                "enum": ["bug", "gap", "risk", "improvement", "pass"],
            },
            "title": {"type": "string"},
            "description": {"type": "string"},
            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "recommendation": {"type": "string"},
        },
        ["category", "title", "description", "severity"],
    ),
]


def get_tools_for_agent(agent_type: AgentType) -> list[dict]:
    mapping = {
        AgentType.ORCHESTRATOR: ORCHESTRATOR_TOOLS,
        AgentType.STRATEGY: STRATEGY_TOOLS,
        AgentType.RESEARCH: RESEARCH_TOOLS,
        AgentType.IMPLEMENTATION: IMPLEMENTATION_TOOLS,
        AgentType.FORECASTING: FORECASTING_TOOLS,
        AgentType.QA: QA_TOOLS,
    }
    return mapping.get(agent_type, SHARED_TOOLS)


# ─── Tool executor ────────────────────────────────────────────────────────────

async def execute_tool(tool_name: str, tool_input: dict, agent_type: str) -> Any:
    """Dispatch a tool call to its implementation and return the result."""

    # ── Shared ──────────────────────────────────────────────────────────────

    if tool_name == "get_project_state":
        project = await state.get_active_project()
        if project is None:
            return {"error": "No active project. Start one with /new-project <name>"}
        tasks = await state.get_tasks()
        strategies = await state.get_strategies()
        forecasts = await state.get_forecasts()
        return {
            "project": project.model_dump(),
            "recent_tasks": [t.model_dump() for t in tasks[:10]],
            "strategies": strategies[:5],
            "forecasts": forecasts[:5],
        }

    if tool_name == "log_activity":
        await state.log(
            agent_type=agent_type,
            action=tool_input.get("action", ""),
            details=tool_input.get("details", ""),
            level=tool_input.get("level", "info"),
        )
        return {"ok": True}

    if tool_name == "send_message":
        from core.models import ChatMessage
        await state.add_message(
            ChatMessage(
                sender=agent_type,
                content=tool_input["content"],
                message_type=tool_input.get("message_type", "chat"),
            )
        )
        return {"ok": True}

    if tool_name == "web_search":
        return await _web_search(tool_input["query"], tool_input.get("focus", ""))

    # ── Orchestrator ─────────────────────────────────────────────────────────

    if tool_name == "create_task":
        project = await state.get_active_project()
        if project is None:
            return {"error": "No active project"}
        task = await state.create_task(
            TaskCreate(
                project_id=project.id,
                agent_type=AgentType(tool_input["agent_type"]),
                title=tool_input.get("title", ""),
                description=tool_input["description"],
                priority=Priority(tool_input.get("priority", "medium")),
                context=tool_input.get("context", {}),
            )
        )
        return {"task_id": task.id, "status": "created"}

    if tool_name == "request_approval":
        project = await state.get_active_project()
        if project is None:
            return {"error": "No active project"}
        decision_id, event = await state.request_approval(
            DecisionCreate(
                project_id=project.id,
                title=tool_input["title"],
                description=tool_input["description"],
                options=tool_input.get("options", ["Approve", "Reject"]),
                recommendation=tool_input.get("recommendation", ""),
                urgency=tool_input.get("urgency", "normal"),
            )
        )
        # Wait for user to respond (with timeout)
        try:
            await asyncio.wait_for(event.wait(), timeout=3600)
            response = state._approval_responses.get(decision_id, "")
            return {"decision_id": decision_id, "response": response}
        except asyncio.TimeoutError:
            return {"decision_id": decision_id, "response": "timeout", "status": "expired"}

    if tool_name == "update_project_field":
        await state.update_project_field(tool_input["field"], tool_input["value"])
        return {"ok": True}

    if tool_name == "communicate_with_external_agent":
        return await _send_acp_message(
            tool_input["agent_name"],
            tool_input["task_type"],
            tool_input["payload"],
        )

    # ── Strategy ─────────────────────────────────────────────────────────────

    if tool_name == "save_strategy":
        project = await state.get_active_project()
        if project is None:
            return {"error": "No active project"}
        await state.save_strategy(
            project.id,
            StrategyEntry(
                title=tool_input["title"],
                content=tool_input["content"],
                phase=tool_input.get("phase", "active"),
                priority=tool_input.get("priority", 5),
            ),
        )
        return {"ok": True}

    if tool_name == "get_strategies":
        return {"strategies": await state.get_strategies()}

    # ── Research ─────────────────────────────────────────────────────────────

    if tool_name == "fetch_url":
        return await _fetch_url(tool_input["url"], tool_input.get("extract", ""))

    # ── Implementation ───────────────────────────────────────────────────────

    if tool_name == "record_implementation":
        await state.log(
            agent_type=agent_type,
            action=f"IMPL: {tool_input['action_title']}",
            details=json.dumps(
                {
                    "description": tool_input.get("description", ""),
                    "outcome": tool_input.get("outcome", ""),
                    "next_steps": tool_input.get("next_steps", []),
                }
            ),
        )
        return {"ok": True}

    if tool_name == "identify_tool_or_resource":
        await state.log(
            agent_type=agent_type,
            action=f"TOOL_IDENTIFIED: {tool_input['name']}",
            details=json.dumps(tool_input),
        )
        return {"ok": True, "recorded": tool_input["name"]}

    # ── Forecasting ──────────────────────────────────────────────────────────

    if tool_name == "save_forecast":
        project = await state.get_active_project()
        if project is None:
            return {"error": "No active project"}
        await state.save_forecast(
            project.id,
            ForecastEntry(
                period=tool_input["period"],
                metric=tool_input["metric"],
                value=tool_input.get("value"),
                narrative=tool_input["narrative"],
                confidence=tool_input.get("confidence", 0.7),
            ),
        )
        return {"ok": True}

    if tool_name == "get_forecasts":
        return {"forecasts": await state.get_forecasts()}

    # ── QA ───────────────────────────────────────────────────────────────────

    if tool_name == "record_qa_finding":
        await state.log(
            agent_type=agent_type,
            action=f"QA_{tool_input['category'].upper()}: {tool_input['title']}",
            details=json.dumps(
                {
                    "description": tool_input.get("description", ""),
                    "severity": tool_input.get("severity", "low"),
                    "recommendation": tool_input.get("recommendation", ""),
                }
            ),
            level="warning" if tool_input.get("severity") in ("high", "critical") else "info",
        )
        return {"ok": True}

    return {"error": f"Unknown tool: {tool_name}"}


# ─── Network helpers ──────────────────────────────────────────────────────────

async def _web_search(query: str, focus: str = "") -> dict:
    """Lightweight DuckDuckGo Instant Answer search (no API key required)."""
    try:
        q = f"{query} {focus}".strip()
        encoded = urllib.parse.quote(q)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers={"User-Agent": "AgentTeam/1.0"})
            data = r.json()
            abstract = data.get("AbstractText", "")
            related = [t.get("Text", "") for t in data.get("RelatedTopics", [])[:5] if "Text" in t]
            return {
                "query": q,
                "abstract": abstract,
                "related_topics": related,
                "source": data.get("AbstractSource", ""),
            }
    except Exception as exc:
        return {"query": query, "error": str(exc), "abstract": "Search unavailable"}


async def _fetch_url(url: str, extract: str = "") -> dict:
    """Fetch plain text from a URL."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "AgentTeam/1.0"})
            # Very naive text extraction
            text = r.text
            # Strip HTML tags simply
            import re
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return {"url": url, "content": text[:4000], "status": r.status_code}
    except Exception as exc:
        return {"url": url, "error": str(exc)}


async def _send_acp_message(agent_name: str, task_type: str, payload: dict) -> dict:
    """Send an ACP message to a registered external agent."""
    import uuid
    from core.models import ACPMessage, ACPMessageType

    endpoint = EXTERNAL_AGENTS.get(agent_name)
    if not endpoint:
        return {
            "error": f"External agent '{agent_name}' not registered. "
            f"Set {agent_name.upper()}_URL in .env"
        }
    msg = ACPMessage(
        message_id=str(uuid.uuid4()),
        type=ACPMessageType.TASK,
        sender="agent_team",
        recipient=agent_name,
        payload={"task_type": task_type, **payload},
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(endpoint, json=msg.model_dump(mode="json"))
            return {"ok": True, "status": r.status_code, "response": r.json()}
    except Exception as exc:
        return {"error": str(exc)}
