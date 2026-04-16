"""Central configuration — loaded once at startup."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ─── Anthropic ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "claude-opus-4-6")
FAST_MODEL: str = os.getenv("FAST_MODEL", "claude-haiku-4-5-20251001")

# ─── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL: str = f"sqlite+aiosqlite:///{DATA_DIR}/agent_team.db"

# ─── ACP ─────────────────────────────────────────────────────────────────────
ACP_HOST: str = os.getenv("ACP_HOST", "0.0.0.0")
ACP_PORT: int = int(os.getenv("ACP_PORT", "8765"))

EXTERNAL_AGENTS: dict[str, str] = {
    name: url
    for name, url in {
        "openclaw": os.getenv("OPENCLAW_URL", ""),
        "paperclip": os.getenv("PAPERCLIP_URL", ""),
    }.items()
    if url
}

# ─── Agent Behaviour ─────────────────────────────────────────────────────────
MAX_TOKENS: int = 8192
MAX_TOOL_ITERATIONS: int = 25
AUTONOMOUS_LOOP_INTERVAL: int = int(os.getenv("AUTONOMOUS_LOOP_INTERVAL", "60"))
APPROVAL_TIMEOUT: int = 3600  # seconds before an unanswered approval expires
