#!/bin/bash
set -e

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="agent-team"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo ""
echo "  AGENT TEAM — Setup & Launch"
echo "  ================================"
echo ""

# ── Pull latest code ──────────────────────────────────────────────────────────
echo "==> Updating code..."
git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || true

# ── Install system deps ───────────────────────────────────────────────────────
echo "==> Installing Python..."
apt-get install -y python3-pip python-is-python3 -qq 2>/dev/null || true

# ── Install Python packages ───────────────────────────────────────────────────
echo "==> Installing packages..."
pip3 install -q -r "$INSTALL_DIR/requirements.txt"

# ── API key ───────────────────────────────────────────────────────────────────
ENV_FILE="$INSTALL_DIR/.env"
if [ -z "$ANTHROPIC_API_KEY" ] && ! grep -q "ANTHROPIC_API_KEY" "$ENV_FILE" 2>/dev/null; then
    echo ""
    echo "  No API key found. You can:"
    echo "  1. Set it now:  export ANTHROPIC_API_KEY=sk-ant-..."
    echo "  2. Enter it in the browser setup page at http://localhost:7860"
    echo ""
fi

# Write key to .env if provided via env var
if [ -n "$ANTHROPIC_API_KEY" ]; then
    touch "$ENV_FILE"
    if ! grep -q "ANTHROPIC_API_KEY" "$ENV_FILE" 2>/dev/null; then
        echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" >> "$ENV_FILE"
        echo "==> API key saved to .env"
    fi
fi

# ── Install as systemd service (runs on boot, auto-restarts) ──────────────────
if command -v systemctl &>/dev/null; then
    echo "==> Installing systemd service..."
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
    echo "  Agent Team is running as a system service."
    echo "  It will start automatically on every reboot."
    echo ""
    echo "  Access:  http://localhost:7860"
    echo ""
    echo "  Commands:"
    echo "    systemctl status $SERVICE_NAME    # check status"
    echo "    journalctl -u $SERVICE_NAME -f    # view logs"
    echo "    systemctl restart $SERVICE_NAME   # restart"
    echo ""
else
    # No systemd — just run directly
    echo ""
    echo "  Starting Agent Team on http://localhost:7860"
    echo "  Press Ctrl+C to stop."
    echo ""
    python3 "$INSTALL_DIR/web_server.py"
fi
