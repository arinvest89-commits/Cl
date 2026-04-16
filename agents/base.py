"""
BaseAgent — the agentic loop engine shared by every specialized agent.

Each subclass defines:
  - AGENT_TYPE: AgentType
  - SYSTEM_PROMPT: str
  - and inherits run() which drives the tool-use loop.
"""
from __future__ import annotations

import asyncio
import json
import traceback
from typing import Any

import anthropic

from config import ANTHROPIC_API_KEY, DEFAULT_MODEL, MAX_TOKENS, MAX_TOOL_ITERATIONS
from core.models import AgentType, ChatMessage
from core.state import state
from core.tools import execute_tool, get_tools_for_agent


class BaseAgent:
    """Drives a Claude agentic loop for a specific project role."""

    AGENT_TYPE: AgentType = AgentType.ORCHESTRATOR
    SYSTEM_PROMPT: str = "You are a helpful agent."

    def __init__(self) -> None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        self._client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        self._tools = get_tools_for_agent(self.AGENT_TYPE)

    # ─── Public API ──────────────────────────────────────────────────────────

    async def run(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        history: list[dict] | None = None,
    ) -> str:
        """
        Execute the agent on `task`.  Returns the agent's final text response.

        Parameters
        ----------
        task:    The task description given to the agent.
        context: Optional dict of structured context merged into the first user message.
        history: Existing message history to continue a conversation.
        """
        state.update_agent_status(self.AGENT_TYPE, "thinking", current_task=task[:80])

        messages: list[dict] = list(history or [])

        # Build initial user message
        user_content = task
        if context:
            user_content += f"\n\n**Context:**\n```json\n{json.dumps(context, indent=2)}\n```"
        messages.append({"role": "user", "content": user_content})

        iteration = 0
        last_text = ""

        try:
            while iteration < MAX_TOOL_ITERATIONS:
                iteration += 1
                state.update_agent_status(
                    self.AGENT_TYPE,
                    "working",
                    last_action=f"LLM call #{iteration}",
                )

                response = await self._client.messages.create(
                    model=DEFAULT_MODEL,
                    max_tokens=MAX_TOKENS,
                    system=self.SYSTEM_PROMPT,
                    tools=self._tools,
                    messages=messages,
                )

                # Collect text and tool_use blocks
                assistant_content: list[dict] = []
                tool_calls: list[dict] = []

                for block in response.content:
                    if block.type == "text":
                        last_text = block.text
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append(
                            {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )
                        tool_calls.append(
                            {"id": block.id, "name": block.name, "input": block.input}
                        )

                messages.append({"role": "assistant", "content": assistant_content})

                # If no tools were called, we're done
                if response.stop_reason == "end_turn" or not tool_calls:
                    break

                # Execute tools and gather results
                tool_results = await self._execute_tool_calls(tool_calls)
                messages.append({"role": "user", "content": tool_results})

        except anthropic.APIStatusError as exc:
            err = f"API error in {self.AGENT_TYPE.value}: {exc.message}"
            await state.log(self.AGENT_TYPE.value, "api_error", err, level="error")
            last_text = f"[{self.AGENT_TYPE.value}] Encountered an API error: {exc.message}"
        except Exception as exc:
            err = traceback.format_exc()
            await state.log(self.AGENT_TYPE.value, "error", err, level="error")
            last_text = f"[{self.AGENT_TYPE.value}] Error: {exc}"
        finally:
            state.update_agent_status(self.AGENT_TYPE, "idle")

        return last_text

    async def chat(
        self,
        user_message: str,
        history: list[dict] | None = None,
    ) -> str:
        """Conversational entry point — used by the UI chat interface."""
        return await self.run(user_message, history=history)

    # ─── Private ─────────────────────────────────────────────────────────────

    async def _execute_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        """Run all tool calls (possibly in parallel) and return tool_result blocks."""
        results = await asyncio.gather(
            *[self._run_one_tool(tc) for tc in tool_calls],
            return_exceptions=False,
        )
        return list(results)

    async def _run_one_tool(self, tool_call: dict) -> dict:
        name = tool_call["name"]
        tool_input = tool_call["input"]

        await state.log(
            self.AGENT_TYPE.value,
            f"tool_call:{name}",
            json.dumps(tool_input)[:300],
        )

        try:
            result = await execute_tool(name, tool_input, self.AGENT_TYPE.value)
        except Exception as exc:
            result = {"error": str(exc)}

        await state.log(
            self.AGENT_TYPE.value,
            f"tool_result:{name}",
            json.dumps(result)[:300],
        )

        return {
            "type": "tool_result",
            "tool_use_id": tool_call["id"],
            "content": json.dumps(result),
        }
