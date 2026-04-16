# Agent Team — Codebase Guide

## Overview
An autonomous agent team for project execution and management, built on Claude's tool-use API.
The team runs inside a Textual TUI and communicates with external agents via ACP (HTTP).

## Quick Start
```bash
pip install -r requirements.txt
cp .env.example .env          # then add your ANTHROPIC_API_KEY
python main.py                # launch the TUI
```

## Architecture

```
main.py                  Entry point (ui / acp / both modes)
config.py                All config — reads from .env

core/
  database.py            SQLAlchemy ORM models + async engine (SQLite)
  models.py              Pydantic schemas for all data types
  state.py               ProjectStateManager singleton — all agents read/write here
  tools.py               Tool implementations + Claude tool schemas per agent

agents/
  base.py                BaseAgent — drives the Claude agentic loop
  orchestrator.py        Project Manager — coordinates all agents, handles user chat
  strategy.py            Strategy & Planning
  research.py            Market research & analysis
  implementation.py      Execution, tool/resource integration
  forecasting.py         Performance & needs forecasting
  qa.py                  Testing & quality assurance

acp/
  protocol.py            ACPRouter — in-process message routing
  server.py              FastAPI HTTP server for inbound ACP messages
  client.py              ACPClient — send messages to external agents

ui/
  app.py                 AgentTeamApp (Textual) — main UI
  panels.py              Custom widgets: AgentStatusWidget, ChatMessageWidget, etc.
  theme.tcss             Textual CSS styling
```

## Key Concepts

### Agentic Loop (agents/base.py)
Each agent drives a Claude tool-use loop: Claude calls tools → tools execute →
results feed back → until `stop_reason == "end_turn"`. Max `MAX_TOOL_ITERATIONS` cycles.

### State Management (core/state.py)
`ProjectStateManager` is a singleton. All agents interact with it via async methods.
It persists to SQLite and emits events via a pub/sub system the UI subscribes to.

### Agent Communication Protocol (acp/)
HTTP-based JSON protocol. External agents (openclaw, paperclip) register their
`/acp/receive` endpoint. The team exposes `POST /acp/receive` for inbound messages.
Message types: `TASK | RESULT | STATUS | REQUEST | RESPONSE | ERROR | HEARTBEAT`

### Approval Workflow
When an agent calls `request_approval`, a `Decision` row is created and an `asyncio.Event`
is returned. The UI shows an approval card. When the user responds, the event fires and
the agent's tool call resolves with the user's decision.

### Autonomous Loop
`OrchestratorAgent.start_autonomous_loop()` runs `_autonomous_loop()` every
`AUTONOMOUS_LOOP_INTERVAL` seconds. It reviews project health and creates/delegates
tasks without user input. Toggle with F2 or `/loop start|stop`.

## Environment Variables
See `.env.example` for all options. Key ones:
- `ANTHROPIC_API_KEY` — required
- `DEFAULT_MODEL` — defaults to `claude-opus-4-6`
- `OPENCLAW_URL` / `PAPERCLIP_URL` — external agent ACP endpoints
- `AUTONOMOUS_LOOP_INTERVAL` — seconds between autonomous cycles (default 60)
- `ACP_PORT` — port for the ACP server (default 8765)

## TUI Commands
| Command | Action |
|---------|--------|
| `/new-project <name>` | 3-step wizard to create a project |
| `/load-project <id>` | Switch active project |
| `/projects` | List all projects |
| `/status` | Full project status |
| `/approve <id>` | Approve a pending decision |
| `/reject <id> [reason]` | Reject a pending decision |
| `/agents` | List connected external agents |
| `/acp-register <name> <url>` | Register an external agent |
| `/tasks` | Show all tasks |
| `/strategies` | Show all strategies |
| `/forecasts` | Show all forecasts |
| `/logs` | Show recent agent logs |
| `/loop start\|stop` | Toggle autonomous loop |
| F2 | Toggle autonomous loop |
| F1 | Show help |

## Adding a New Agent
1. Create `agents/my_agent.py` extending `BaseAgent`
2. Set `AGENT_TYPE` and `SYSTEM_PROMPT`
3. Add tool schemas in `core/tools.py` (`MY_AGENT_TOOLS`)
4. Add tool implementations to `execute_tool()` in `core/tools.py`
5. Register in `ui/app.py` → `_init_agents()`
6. Register in `orchestrator.py` → `initialise_project()` system prompt

## External Agent Integration (ACP)
To connect openclaw or paperclip:
1. Set their endpoint in `.env` (`OPENCLAW_URL=http://host:port/acp/receive`)
2. Or at runtime: `/acp-register openclaw http://host:port/acp/receive`
3. They must accept POST `application/json` at their `/acp/receive` endpoint
4. This team's ACP server listens on `ACP_PORT` (default 8765)
