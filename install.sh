#!/usr/bin/env bash
# Hermes Companion Daemon — One-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/klautimus/hermes-companion-daemon/main/install.sh | bash
# Or: ./install.sh [--docker] [--port 8777] [--hermes-api http://localhost:8642]

set -euo pipefail

COMPANION_USER="companion"
INSTALL_DIR="/opt/hermes-companion"
CONFIG_DIR="/etc/hermes-companion"
DATA_DIR="/var/lib/hermes-companion"
PORT="8777"
HERMES_API="http://localhost:8642"
USE_DOCKER=false

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --docker)     USE_DOCKER=true; shift ;;
        --port)       PORT="$2"; shift 2 ;;
        --hermes-api) HERMES_API="$2"; shift 2 ;;
        *)            echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         Hermes Companion Daemon Installer v0.2.0           ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Mode:    $([ "$USE_DOCKER" = true ] && echo 'Docker' || echo 'Systemd')"
echo "║  Port:    $PORT"
echo "║  Hermes:  $HERMES_API"
echo "╚══════════════════════════════════════════════════════════════╝"

if [ "$USE_DOCKER" = true ]; then
    install_docker
else
    install_systemd
fi

echo ""
echo "✅ Hermes Companion Daemon installed successfully!"
echo ""
echo "Next steps:"
echo "  1. Configure: edit $COMPANION_HOST/auth.json (or run setup wizard)"
echo "  2. Android APK: https://github.com/klautimus/hermes-companion/releases"
echo "  3. Tunnel (optional): cloudflared tunnel --url http://localhost:$PORT"
echo ""

install_systemd() {
    echo "[1/5] Checking prerequisites..."
    
    # Check Python
    if ! command -v python3 &>/dev/null; then
        echo "ERROR: python3 not found. Install Python 3.10+ first."
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    echo "  Python $PYTHON_VERSION detected"
    
    # Check hermes CLI
    if command -v hermes &>/dev/null; then
        echo "  hermes CLI detected: $(hermes --version 2>/dev/null || echo 'unknown version')"
    else
        echo "  WARNING: hermes CLI not found on PATH. Kanban features will not work."
        echo "  Install from: https://github.com/nousresearch/hermes-agent"
    fi

    echo "[2/5] Creating directories..."
    sudo mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$DATA_DIR"
    
    echo "[3/5] Installing Python package..."
    # Create a virtual environment
    sudo python3 -m venv "$INSTALL_DIR/venv"
    sudo "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    sudo "$INSTALL_DIR/venv/bin/pip" install -e "$PWD"
    
    echo "[4/5] Installing systemd unit..."
    sudo tee /etc/systemd/system/companion.service > /dev/null <<EOF
[Unit]
Description=Hermes Companion Daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/hermes-companion serve --port $PORT --hermes-api $HERMES_API
Restart=on-failure
RestartSec=5
Environment=HERMES_API=$HERMES_API
Environment=PYTHONUNBUFFERED=1
Environment=CONFIG_DIR=$CONFIG_DIR
Environment=DATA_DIR=$DATA_DIR
Environment=PATH=$INSTALL_DIR/venv/bin:/home/$(whoami)/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
EOF

    echo "[5/5] Starting service..."
    sudo systemctl daemon-reload
    sudo systemctl enable hermes-companion
    sudo systemctl start hermes-companion
    
    sleep 2
    if systemctl is-active --quiet hermes-companion; then
        echo "  ✅ Service is running on port $PORT"
    else
        echo "  ⚠️  Service did not start. Check: journalctl -u hermes-companion -n 20"
    fi
}

install_docker() {
    echo "[1/3] Checking Docker..."
    if ! command -v docker &>/dev/null; then
        echo "ERROR: Docker not found. Install Docker first."
        exit 1
    fi
    
    echo "[2/3] Building Docker image..."
    docker build -t hermes-companion:latest .
    
    echo "[3/3] Starting container..."
    docker run -d \
        --name hermes-companion \
        --restart unless-stopped \
        -p "$PORT:8777" \
        -e "HERMES_API=$HERMES_API" \
        -v "companion-data:/data" \
        hermes-companion:latest
    
    sleep 3
    if docker ps | grep -q hermes-companion; then
        echo "  ✅ Container is running on port $PORT"
    else
        echo "  ⚠️  Container did not start. Check: docker logs hermes-companion"
    fi
}
