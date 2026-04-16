"""
ACP Client — helpers for the agent team to communicate with external agents.
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx

from acp.protocol import make_result_message, make_task_message, router as acp_router
from config import EXTERNAL_AGENTS
from core.models import ACPMessage, ACPMessageType
from core.state import state


class ACPClient:
    """Thin async client for sending ACP messages to external agents."""

    def __init__(self, sender_name: str = "agent_team") -> None:
        self.sender = sender_name

    async def send_task(
        self,
        recipient: str,
        task_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Fire-and-forget task dispatch (no reply expected)."""
        endpoint = EXTERNAL_AGENTS.get(recipient) or acp_router.registered_agents.get(recipient)
        if not endpoint:
            return {"error": f"Agent '{recipient}' not registered"}

        msg = make_task_message(self.sender, recipient, task_type, payload, correlation_id)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(endpoint, json=msg.model_dump(mode="json"))
                await state.log(
                    "acp_client",
                    f"sent_task:{task_type}",
                    f"to={recipient} status={r.status_code}",
                )
                return {"ok": True, "status": r.status_code}
        except Exception as exc:
            await state.log("acp_client", "send_error", str(exc), level="error")
            return {"error": str(exc)}

    async def request_reply(
        self,
        recipient: str,
        payload: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Send a REQUEST and wait for a RESPONSE."""
        try:
            return await acp_router.request(recipient, payload, timeout=timeout)
        except (TimeoutError, ValueError) as exc:
            return {"error": str(exc)}

    async def broadcast(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Send a message to all registered external agents."""
        results = []
        all_agents = {**EXTERNAL_AGENTS, **acp_router.registered_agents}
        for name in all_agents:
            r = await self.send_task(name, "broadcast", payload)
            results.append({"agent": name, **r})
        return results

    async def heartbeat_all(self) -> list[dict[str, Any]]:
        """Ping all registered agents to check liveness."""
        results = []
        all_agents = {**EXTERNAL_AGENTS, **acp_router.registered_agents}
        for name, endpoint in all_agents.items():
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(endpoint.replace("/acp/receive", "/acp/status"))
                    results.append({"agent": name, "ok": r.status_code == 200})
            except Exception:
                results.append({"agent": name, "ok": False})
        return results


# ─── Singleton ────────────────────────────────────────────────────────────────
acp_client = ACPClient()
