"""
Approval system — manages key decisions that require user sign-off.
Auto-approves low-impact decisions; routes high-impact ones to the user.
"""
from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Callable, Optional

from .models import ApprovalRequest, ApprovalStatus, AgentRole
from .memory import MemoryManager


IMPACT_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class ApprovalManager:
    """
    Manages the approval queue and auto-approval logic.
    Auto-approves anything below the configured threshold.
    """

    def __init__(
        self,
        memory: MemoryManager,
        auto_approve_threshold: str = "low",
        notify_callback: Optional[Callable] = None,
    ):
        self.memory = memory
        self.threshold = auto_approve_threshold
        self._notify = notify_callback
        self._pending: dict[str, ApprovalRequest] = {}
        self._resolution_events: dict[str, asyncio.Event] = {}

    async def submit(self, request: ApprovalRequest) -> ApprovalStatus:
        """
        Submit an approval request.
        Auto-approves if below threshold, otherwise queues for user.
        Returns the final status (may wait for user if above threshold).
        """
        impact = request.impact_level
        if IMPACT_RANK.get(impact, 1) <= IMPACT_RANK.get(self.threshold, 0):
            request.status = ApprovalStatus.AUTO_APPROVED
            request.chosen_option = request.recommended_option
            request.resolved_at = datetime.utcnow()
            request.resolution_notes = "Auto-approved (below threshold)"
            await self.memory.save_approval(request)
            return ApprovalStatus.AUTO_APPROVED

        self._pending[request.id] = request
        event = asyncio.Event()
        self._resolution_events[request.id] = event
        await self.memory.save_approval(request)
        if self._notify:
            self._notify(request)
        return ApprovalStatus.PENDING

    async def wait_for_resolution(self, request_id: str, timeout: float = 3600.0) -> ApprovalStatus:
        """Block until the user resolves an approval (or timeout)."""
        event = self._resolution_events.get(request_id)
        if not event:
            return ApprovalStatus.PENDING
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        request = await self.memory.load_approvals()
        for r in request:
            if r.id == request_id:
                return r.status
        return ApprovalStatus.PENDING

    async def resolve(
        self,
        request_id: str,
        choice: str,
        notes: str = "",
        approved: bool = True,
    ) -> Optional[ApprovalRequest]:
        """Resolve a pending approval request."""
        approvals = await self.memory.load_approvals()
        request = next((a for a in approvals if a.id == request_id), None)
        if not request:
            return None

        request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        request.chosen_option = choice
        request.resolution_notes = notes
        request.resolved_at = datetime.utcnow()

        await self.memory.save_approval(request)
        self._pending.pop(request_id, None)
        event = self._resolution_events.pop(request_id, None)
        if event:
            event.set()
        return request

    async def get_pending(self) -> list[ApprovalRequest]:
        return await self.memory.load_approvals(pending_only=True)

    async def get_all(self) -> list[ApprovalRequest]:
        return await self.memory.load_approvals()

    def pending_count(self) -> int:
        return len(self._pending)
