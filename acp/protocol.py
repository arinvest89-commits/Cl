"""
Agent Communication Protocol (ACP)
===================================
A lightweight HTTP-based messaging protocol for autonomous agent interoperability.

Message format (JSON):
  {
    "message_id": "<uuid>",
    "type":       "TASK|RESULT|STATUS|REQUEST|RESPONSE|ERROR|HEARTBEAT",
    "sender":     "<agent_name>",
    "recipient":  "<agent_name>|broadcast",
    "payload":    { ... },
    "correlation_id": "<uuid>|null",   // links responses to requests
    "timestamp":  "<ISO-8601>"
  }

Endpoints exposed by this server:
  POST /acp/receive     — receive a message from an external agent
  GET  /acp/status      — health + registered agents
  POST /acp/register    — register an external agent endpoint
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Callable

from core.models import ACPMessage, ACPMessageType


class ACPRouter:
    """In-process message router + registry for connected external agents."""

    def __init__(self) -> None:
        self._handlers: dict[ACPMessageType, list[Callable]] = {}
        self._registered_agents: dict[str, str] = {}     # name → endpoint URL
        self._pending_responses: dict[str, asyncio.Future] = {}  # correlation_id → future

    # ─── Registration ─────────────────────────────────────────────────────────

    def register_external_agent(self, name: str, endpoint: str) -> None:
        self._registered_agents[name] = endpoint

    def unregister_external_agent(self, name: str) -> None:
        self._registered_agents.pop(name, None)

    @property
    def registered_agents(self) -> dict[str, str]:
        return dict(self._registered_agents)

    # ─── Subscription ─────────────────────────────────────────────────────────

    def on(self, message_type: ACPMessageType, handler: Callable) -> None:
        """Register an async or sync handler for a message type."""
        self._handlers.setdefault(message_type, []).append(handler)

    # ─── Dispatch ────────────────────────────────────────────────────────────

    async def dispatch(self, message: ACPMessage) -> dict[str, Any]:
        """
        Route an incoming message to registered handlers.
        Returns a dict suitable for the HTTP response body.
        """
        results = []
        for handler in self._handlers.get(message.type, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(message)
                else:
                    result = handler(message)
                results.append(result)
            except Exception as exc:
                results.append({"error": str(exc)})

        # If this is a RESPONSE to a pending request, resolve the future
        if message.correlation_id and message.correlation_id in self._pending_responses:
            fut = self._pending_responses.pop(message.correlation_id)
            if not fut.done():
                fut.set_result(message.payload)

        return {
            "ok": True,
            "message_id": message.message_id,
            "handlers_invoked": len(results),
            "results": results,
        }

    # ─── Outbound request (request-reply) ────────────────────────────────────

    async def request(
        self,
        recipient: str,
        payload: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """
        Send a REQUEST message to a named external agent and await its RESPONSE.
        Raises TimeoutError if no response arrives within `timeout` seconds.
        """
        import httpx

        endpoint = self._registered_agents.get(recipient)
        if not endpoint:
            raise ValueError(f"Agent '{recipient}' is not registered. Use register_external_agent().")

        corr_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_responses[corr_id] = fut

        msg = ACPMessage(
            message_id=str(uuid.uuid4()),
            type=ACPMessageType.REQUEST,
            sender="agent_team",
            recipient=recipient,
            payload=payload,
            correlation_id=corr_id,
        )

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(endpoint, json=msg.model_dump(mode="json"))

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_responses.pop(corr_id, None)
            raise TimeoutError(f"No response from '{recipient}' within {timeout}s")


def make_task_message(
    sender: str,
    recipient: str,
    task_type: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> ACPMessage:
    return ACPMessage(
        message_id=str(uuid.uuid4()),
        type=ACPMessageType.TASK,
        sender=sender,
        recipient=recipient,
        payload={"task_type": task_type, **payload},
        correlation_id=correlation_id,
    )


def make_result_message(
    sender: str,
    recipient: str,
    result: Any,
    correlation_id: str | None = None,
) -> ACPMessage:
    return ACPMessage(
        message_id=str(uuid.uuid4()),
        type=ACPMessageType.RESULT,
        sender=sender,
        recipient=recipient,
        payload={"result": result},
        correlation_id=correlation_id,
    )


# ─── Singleton router ────────────────────────────────────────────────────────
router = ACPRouter()
