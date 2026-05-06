"""
Auto-discovers OpenClaw, Paperclip, and any other agents installed on the host
by scanning well-known config locations, then registers them with the ACP client.
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agent_discovery")

# Where each known agent stores its config
KNOWN_AGENT_CONFIG_PATHS = {
    "openclaw": [
        Path.home() / ".openclaw" / "config.json",
        Path.home() / ".openclaw" / "settings.json",
        Path("/etc/openclaw/config.json"),
    ],
    "paperclip": [
        Path.home() / ".paperclip" / "config.json",
        Path.home() / ".paperclip" / "settings.json",
        Path("/etc/paperclip/config.json"),
    ],
}

# Default ports to probe if no config found
AGENT_DEFAULT_PORTS = {
    "openclaw": 8766,
    "paperclip": 8767,
}

AGENT_CAPABILITIES = {
    "openclaw": ["research", "analysis", "web_browsing", "data_extraction"],
    "paperclip": ["task_management", "scheduling", "resource_allocation", "document_processing"],
}


def _read_config(path: Path) -> Optional[dict]:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def discover_local_agents() -> dict[str, dict]:
    """
    Scans the host for installed agents and returns their ACP config.
    Falls back to default ports when no config file is found.
    """
    found: dict[str, dict] = {}

    for agent_name, config_paths in KNOWN_AGENT_CONFIG_PATHS.items():
        endpoint = None

        # Try reading from config file
        for path in config_paths:
            cfg = _read_config(path)
            if cfg:
                # Support various config key names agents might use
                port = (
                    cfg.get("acp_port")
                    or cfg.get("port")
                    or cfg.get("server_port")
                    or AGENT_DEFAULT_PORTS.get(agent_name)
                )
                host = cfg.get("acp_host") or cfg.get("host") or "localhost"
                endpoint = f"http://{host}:{port}"
                logger.info(f"Discovered {agent_name} config at {path} → {endpoint}")
                break

        # Fall back to default port
        if not endpoint:
            # Check if the agent directory exists at all (agent is installed)
            agent_dir = Path.home() / f".{agent_name}"
            if agent_dir.exists() and agent_dir.is_dir():
                port = AGENT_DEFAULT_PORTS[agent_name]
                endpoint = f"http://localhost:{port}"
                logger.info(f"Found {agent_name} directory, using default endpoint {endpoint}")

        if endpoint:
            found[agent_name] = {
                "endpoint": endpoint,
                "capabilities": AGENT_CAPABILITIES.get(agent_name, []),
                "discovered": True,
            }

    if found:
        logger.info(f"Agent discovery complete: {list(found.keys())}")
    else:
        logger.info("No local agents discovered")

    return found


def merge_with_config(config_agents: dict, discovered: dict) -> dict:
    """Merge discovered agents with any manually configured ones (config wins on conflict)."""
    merged = dict(discovered)
    for name, cfg in config_agents.items():
        merged[name] = cfg  # manual config always wins
    return merged
