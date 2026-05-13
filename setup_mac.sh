#!/bin/bash
# Mac setup — run this once in your terminal
set -e

echo ""
echo "  AGENT TEAM — Mac Setup"
echo "  ========================"
echo ""

# ── Install Homebrew if missing ───────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
  echo "==> Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  # Add to PATH for Apple Silicon
  echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zshrc
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# ── Install Python 3 ──────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "==> Installing Python 3..."
  brew install python
fi

# ── Install pip if missing ────────────────────────────────────────────────────
if ! command -v pip3 &>/dev/null; then
  curl -sS https://bootstrap.pypa.io/get-pip.py | python3
fi

# ── Clone or update the repo ──────────────────────────────────────────────────
INSTALL_DIR="$HOME/agent-team"
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "==> Updating code..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "==> Downloading Agent Team..."
  git clone https://github.com/arinvest89-commits/Cl "$INSTALL_DIR"
fi

# ── Install Python packages ───────────────────────────────────────────────────
echo "==> Installing packages..."
pip3 install -q -r "$INSTALL_DIR/requirements.txt"

# ── API key ───────────────────────────────────────────────────────────────────
ENV_FILE="$INSTALL_DIR/.env"
if [ -n "$ANTHROPIC_API_KEY" ]; then
  echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" > "$ENV_FILE"
elif [ ! -f "$ENV_FILE" ]; then
  echo ""
  echo "  Enter your Anthropic API key (or press Enter to set it in the browser):"
  read -r -s KEY
  if [ -n "$KEY" ]; then
    echo "ANTHROPIC_API_KEY=$KEY" > "$ENV_FILE"
    echo "  Key saved."
  fi
fi

# ── Launch ────────────────────────────────────────────────────────────────────
echo ""
echo "  Starting Agent Team..."
echo "  Opening http://localhost:7860"
echo ""
open "http://localhost:7860" 2>/dev/null || true
python3 "$INSTALL_DIR/web_server.py"
