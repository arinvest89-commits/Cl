"""
Agent Communication Protocol (ACP)
Provides an HTTP-based communication layer allowing the agent team to
autonomously communicate with external agents (OpenClaw, Paperclip, etc.)
"""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

import aiohttp
from aiohttp import web

from .models import ACPMessage

logger = logging.getLogger("acp")


class ACPServer:
    """
    Local HTTP server that listens for inbound ACP messages from
    external agents and routes them to the orchestrator.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        message_handler: Optional[Callable] = None,
    ):
        self.host = host
        self.port = port
        self._message_handler = message_handler
        self._app = web.Application()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_post("/acp/message", self._handle_message)
        self._app.router.add_get("/acp/health", self._handle_health)
        self._app.router.add_get("/acp/capabilities", self._handle_capabilities)

    async def _handle_message(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            message = ACPMessage(**data)
            await self._inbox.put(message)

            if self._message_handler:
                response_msg = await self._message_handler(message)
                if response_msg:
                    return web.json_response(json.loads(response_msg.model_dump_json()))

            return web.json_response({"status": "accepted", "id": message.id})
        except Exception as e:
            logger.error(f"ACP message error: {e}")
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "online",
            "agent": "project-manager-team",
            "timestamp": datetime.utcnow().isoformat(),
            "inbox_size": self._inbox.qsize(),
        })

    async def _handle_capabilities(self, request: web.Request) -> web.Response:
        return web.json_response({
            "agent": "project-manager-team",
            "capabilities": [
                "project_management",
                "strategy_development",
                "market_research",
                "forecasting",
                "qa_testing",
                "tool_integration",
                "operations_management",
            ],
            "actions": [
                "status_request",
                "task_handoff",
                "data_share",
                "broadcast",
                "forecast_request",
                "research_request",
            ],
            "protocol_version": "1.0",
        })

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info(f"ACP server listening on {self.host}:{self.port}")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        logger.info("ACP server stopped")


class ACPClient:
    """
    Client for sending ACP messages to external agents.
    Handles connection failures gracefully.
    """

    def __init__(self, known_agents: dict[str, dict[str, Any]]):
        self._agents = known_agents  # {name: {endpoint, capabilities}}
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=30)
        self._status: dict[str, str] = {name: "unknown" for name in known_agents}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def send(
        self,
        recipient: str,
        action: str,
        payload: dict[str, Any],
        requires_response: bool = False,
    ) -> Optional[ACPMessage]:
        """Send a message to a named external agent."""
        agent_config = self._agents.get(recipient)
        if not agent_config:
            logger.warning(f"Unknown ACP agent: {recipient}")
            return None

        message = ACPMessage(
            sender="project-manager-team",
            recipient=recipient,
            message_type="request" if requires_response else "broadcast",
            action=action,
            payload=payload,
            requires_response=requires_response,
        )

        endpoint = f"{agent_config['endpoint']}/acp/message"
        session = await self._get_session()
        try:
            async with session.post(
                endpoint,
                json=json.loads(message.model_dump_json()),
                timeout=self._timeout,
            ) as resp:
                self._status[recipient] = "online"
                if requires_response and resp.status == 200:
                    data = await resp.json()
                    return ACPMessage(**data)
                return None
        except asyncio.TimeoutError:
            self._status[recipient] = "timeout"
            logger.warning(f"ACP timeout: {recipient}")
            return None
        except aiohttp.ClientConnectorError:
            self._status[recipient] = "offline"
            logger.warning(f"ACP agent offline: {recipient}")
            return None
        except Exception as e:
            self._status[recipient] = "error"
            logger.error(f"ACP send error to {recipient}: {e}")
            return None

    async def broadcast(self, message: str, payload: Optional[dict] = None) -> dict[str, bool]:
        """Broadcast a message to all known agents."""
        results = {}
        tasks = []
        for agent_name in self._agents:
            tasks.append((
                agent_name,
                self.send(agent_name, "broadcast", {
                    "message": message,
                    **(payload or {}),
                })
            ))
        responses = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
        for i, (name, _) in enumerate(tasks):
            results[name] = not isinstance(responses[i], Exception) and self._status.get(name) == "online"
        return results

    async def ping_all(self) -> dict[str, bool]:
        """Check which external agents are online."""
        results = {}
        session = await self._get_session()
        for name, config in self._agents.items():
            try:
                async with session.get(
                    f"{config['endpoint']}/acp/health",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    results[name] = resp.status == 200
                    self._status[name] = "online" if resp.status == 200 else "error"
            except Exception:
                results[name] = False
                self._status[name] = "offline"
        return results

    def agent_statuses(self) -> dict[str, str]:
        return dict(self._status)

    async def request_from(self, agent_name: str, action: str, payload: dict) -> Optional[dict]:
        """Send a request and get a response from an external agent."""
        response = await self.send(agent_name, action, payload, requires_response=True)
        if response:
            return response.payload
        return None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class ACPManager:
    """Manages both the ACP server and client."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        known_agents: Optional[dict] = None,
        message_handler: Optional[Callable] = None,
    ):
        self.server = ACPServer(host, port, message_handler)
        self.client = ACPClient(known_agents or {})
        self._running = False

    async def start(self) -> None:
        try:
            await self.server.start()
            self._running = True
        except Exception as e:
            logger.warning(f"ACP server could not start (non-fatal): {e}")

    async def stop(self) -> None:
        await self.server.stop()
        await self.client.close()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def discover_agents(self) -> dict[str, bool]:
        return await self.client.ping_all()

    async def send_to(self, agent: str, action: str, payload: dict, **kwargs) -> Optional[ACPMessage]:
        return await self.client.send(agent, action, payload, **kwargs)

    async def broadcast(self, message: str, payload: Optional[dict] = None) -> dict[str, bool]:
        return await self.client.broadcast(message, payload)

    def agent_statuses(self) -> dict[str, str]:
        return self.client.agent_statuses()
