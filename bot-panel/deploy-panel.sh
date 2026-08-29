#!/bin/bash
set -e

# ─────────────────────────────────────────────────────────────
#  Bot Panel — One-line VPS deploy script
#  Usage:
#    sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/dsmrpha-rgb/automated-bot/master/bot-panel/deploy-panel.sh)"
# ─────────────────────────────────────────────────────────────

REPO="https://github.com/dsmrpha-rgb/automated-bot.git"
CLONE_DIR="/opt/automated-bot-repo"
PANEL_DIR="/opt/bot-panel"
SERVICE_NAME="bot-panel"
PYTHON="python3"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { printf "${CYAN}[INFO]${NC}  %s\n" "$1"; }
ok()    { printf "${GREEN}[ OK ]${NC}  %s\n" "$1"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$1"; }
err()   { printf "${RED}[ERR ]${NC}  %s\n" "$1"; exit 1; }

[ "$(id -u)" -ne 0 ] && err "Run this script as root (sudo)."

# ── Install system deps ─────────────────────────────────────
info "Installing system packages..."
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git curl > /dev/null
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip git curl > /dev/null
elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip git curl > /dev/null
fi
ok "System packages ready."

# ── Clone or pull the repo ──────────────────────────────────
if [ -d "$CLONE_DIR/.git" ]; then
    info "Pulling latest from git..."
    cd "$CLONE_DIR"
    git fetch --all
    git reset --hard origin/master
else
    info "Cloning repository..."
    git clone "$REPO" "$CLONE_DIR"
fi
ok "Repo up to date."

# ── Copy panel files (preserve .env and venv) ───────────────
info "Syncing panel files..."
mkdir -p "$PANEL_DIR/templates"
cp "$CLONE_DIR/bot-panel/app.py" "$PANEL_DIR/app.py"
cp "$CLONE_DIR/bot-panel/requirements.txt" "$PANEL_DIR/requirements.txt"
cp "$CLONE_DIR"/bot-panel/templates/*.html "$PANEL_DIR/templates/"
if [ -d "$CLONE_DIR/bot-panel/static" ]; then
    cp -r "$CLONE_DIR/bot-panel/static" "$PANEL_DIR/"
fi
ok "Panel files synced."

cd "$PANEL_DIR"

# ── Python venv ─────────────────────────────────────────────
info "Setting up Python environment..."
$PYTHON -m venv "$PANEL_DIR/venv"
"$PANEL_DIR/venv/bin/pip" install --upgrade pip -q
"$PANEL_DIR/venv/bin/pip" install -r requirements.txt -q
ok "Dependencies installed."

# ── Panel credentials ───────────────────────────────────────
if [ -f "$PANEL_DIR/.env" ]; then
    warn ".env already exists. Keeping current credentials."
else
    echo ""
    printf "${CYAN}══════════════════════════════════════════${NC}\n"
    printf "${CYAN}       Panel Credentials Setup${NC}\n"
    printf "${CYAN}══════════════════════════════════════════${NC}\n"
    echo ""

    printf "Choose admin username [admin]: "
    read -r P_USER
    P_USER=${P_USER:-admin}

    printf "Choose admin password: "
    read -r P_PASS

    if [ -z "$P_PASS" ]; then
        P_PASS=$(openssl rand -hex 12)
        warn "No password entered. Generated: $P_PASS"
    fi

    printf "Panel port [8080]: "
    read -r P_PORT
    P_PORT=${P_PORT:-8080}

    cat > "$PANEL_DIR/.env" <<ENVEOF
PANEL_USER=$P_USER
PANEL_PASS=$P_PASS
PANEL_PORT=$P_PORT
PANEL_SECRET=$(openssl rand -hex 32)
ENVEOF

    chmod 600 "$PANEL_DIR/.env"
    ok "Credentials saved."
fi

# Read port from .env
P_PORT=$(grep -oP 'PANEL_PORT=\K.*' "$PANEL_DIR/.env" 2>/dev/null || echo "8080")

# ── Config + backup dirs ───────────────────────────────────
mkdir -p /etc/bot-panel
mkdir -p /var/backups/bot-panel

# ── Systemd service ─────────────────────────────────────────
info "Creating systemd service..."

cat > /etc/systemd/system/${SERVICE_NAME}.service <<SVCEOF
[Unit]
Description=Bot Management Panel
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PANEL_DIR
EnvironmentFile=$PANEL_DIR/.env
ExecStart=$PANEL_DIR/venv/bin/gunicorn -b 0.0.0.0:${P_PORT} -w 2 --timeout 120 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
ok "Panel service started."

# ── Detect existing bot installations ───────────────────────
info "Scanning for existing bot services..."
FOUND=0

if [ ! -f /etc/bot-panel/bots.json ]; then
    echo '{"bots":{}}' > /etc/bot-panel/bots.json
fi

# Check for automated-bot (from deploy.sh)
if [ -f /etc/systemd/system/automated-bot.service ] && [ -d /opt/automated-bot ]; then
    $PYTHON -c "
import json
p = '/etc/bot-panel/bots.json'
c = json.load(open(p))
if 'automated-bot' not in c.get('bots', {}):
    c.setdefault('bots', {})['automated-bot'] = {
        'dir': '/opt/automated-bot',
        'service': 'automated-bot',
        'display_name': 'Automated Bot'
    }
    json.dump(c, open(p, 'w'), indent=2)
    print('  -> Registered: automated-bot')
else:
    print('  -> Already registered: automated-bot')
"
    FOUND=1
fi

# Scan for any bot-* services
for svc_file in /etc/systemd/system/bot-*.service; do
    [ -f "$svc_file" ] || continue
    svc_name=$(basename "$svc_file" .service)
    bot_name=${svc_name#bot-}
    bot_dir=$(grep -oP 'WorkingDirectory=\K.*' "$svc_file" 2>/dev/null || true)
    if [ -n "$bot_dir" ] && [ -d "$bot_dir" ]; then
        $PYTHON -c "
import json
p = '/etc/bot-panel/bots.json'
c = json.load(open(p))
name = '$bot_name'
if name not in c.get('bots', {}):
    c.setdefault('bots', {})[name] = {
        'dir': '$bot_dir',
        'service': '$svc_name',
        'display_name': name
    }
    json.dump(c, open(p, 'w'), indent=2)
    print('  -> Registered: ' + name)
"
        FOUND=1
    fi
done

if [ "$FOUND" -eq 0 ]; then
    info "No existing bots found. Add them from the dashboard."
fi

# ── Get server IP ───────────────────────────────────────────
SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

# ── Done ────────────────────────────────────────────────────
echo ""
printf "${GREEN}══════════════════════════════════════════${NC}\n"
printf "${GREEN}       Panel deployed successfully!${NC}\n"
printf "${GREEN}══════════════════════════════════════════${NC}\n"
echo ""
printf "  ${CYAN}URL:${NC}      http://${SERVER_IP}:${P_PORT}\n"
printf "  ${CYAN}User:${NC}     ${P_USER:-admin}\n"
echo ""
info "Commands:"
echo "  Status:   systemctl status $SERVICE_NAME"
echo "  Logs:     journalctl -u $SERVICE_NAME -f"
echo "  Restart:  systemctl restart $SERVICE_NAME"
echo "  Config:   nano $PANEL_DIR/.env"
echo ""
echo "  To update the panel later, just run this script again."
echo ""
warn "For production: set up a reverse proxy (nginx) with HTTPS!"
echo ""
