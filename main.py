"""
Agent Team — Entry Point
========================

Usage:
  python main.py              # Launch the TUI (default)
  python main.py ui           # Launch the TUI explicitly
  python main.py acp          # Start only the ACP server (for external agents)
  python main.py both         # Start TUI + ACP server together

Environment:
  Copy .env.example to .env and set ANTHROPIC_API_KEY before running.
"""
from __future__ import annotations

import asyncio
import sys


def main() -> None:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "ui"

    if mode in ("ui", ""):
        _launch_ui()
    elif mode == "acp":
        _launch_acp()
    elif mode == "both":
        _launch_both()
    else:
        print(__doc__)
        sys.exit(1)


def _launch_ui() -> None:
    """Launch the Textual TUI."""
    from ui.app import AgentTeamApp
    app = AgentTeamApp()
    app.run()


def _launch_acp() -> None:
    """Start only the ACP HTTP server."""
    from acp.server import run_server
    from config import ACP_HOST, ACP_PORT
    print(f"Starting ACP server on {ACP_HOST}:{ACP_PORT} ...")
    run_server()


def _launch_both() -> None:
    """Start the ACP server as a background task and then launch the TUI."""
    import threading
    from acp.server import run_server

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    _launch_ui()


if __name__ == "__main__":
    main()
