#!/bin/bash
set -e

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"

echo ""
echo "  AGENT TEAM — Setup & Launch"
echo "  ================================"
echo "  Platform: $OS"
echo ""

# ── Pull latest code ──────────────────────────────────────────────────────────
echo "==> Updating code..."
git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || true

# ── Install Python deps per platform ─────────────────────────────────────────
if [ "$OS" = "Darwin" ]; then
    # Mac
    if ! command -v brew &>/dev/null; then
        echo "==> Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || eval "$(/usr/local/bin/brew shellenv)"
    fi
    if ! command -v python3 &>/dev/null; then
        echo "==> Installing Python 3..."
        brew install python
    fi
    PIP="pip3"
    PYTHON="python3"
else
    # Linux
    echo "==> Installing Python..."
    apt-get install -y python3-pip python-is-python3 -qq 2>/dev/null || true
    PIP="pip3"
    PYTHON="python3"
fi

# ── Install Python packages ───────────────────────────────────────────────────
echo "==> Installing packages..."
$PIP install -q -r "$INSTALL_DIR/requirements.txt"

# ── API key ───────────────────────────────────────────────────────────────────
ENV_FILE="$INSTALL_DIR/.env"
if [ -n "$ANTHROPIC_API_KEY" ]; then
    touch "$ENV_FILE"
    if ! grep -q "ANTHROPIC_API_KEY" "$ENV_FILE" 2>/dev/null; then
        echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" >> "$ENV_FILE"
        echo "==> API key saved to .env"
    fi
elif ! grep -q "ANTHROPIC_API_KEY" "$ENV_FILE" 2>/dev/null; then
    echo ""
    echo "  No API key set. Open http://localhost:7860 to enter it in the browser."
    echo ""
fi

# ── Launch: systemd on Linux, direct on Mac ───────────────────────────────────
if [ "$OS" = "Darwin" ]; then
    echo ""
    echo "  Starting Agent Team on http://localhost:7860"
    echo "  Press Ctrl+C to stop."
    echo ""
    open "http://localhost:7860" 2>/dev/null || true
    $PYTHON "$INSTALL_DIR/web_server.py"

elif command -v systemctl &>/dev/null; then
    SERVICE_NAME="agent-team"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    echo "==> Installing as system service..."
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Agent Team — Project Management & Execution System
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/web_server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=agent-team

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" --quiet
    systemctl restart "$SERVICE_NAME"
    echo ""
    echo "  Running as a system service — starts automatically on reboot."
    echo ""
    echo "  http://localhost:7860"
    echo ""
    echo "  systemctl status $SERVICE_NAME"
    echo "  journalctl -u $SERVICE_NAME -f"
    echo ""

else
    echo ""
    echo "  Starting Agent Team on http://localhost:7860"
    $PYTHON "$INSTALL_DIR/web_server.py"
fi
