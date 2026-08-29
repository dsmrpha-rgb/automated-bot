#!/bin/bash
set -e

# ─────────────────────────────────────────────────────────────
#  Mamahadesa Bot — One-line VPS deploy script
#  Usage:
#    sudo sh -c "$(curl -fsSL https://raw.githubusercontent.com/dsmrpha-rgb/automated-bot/master/scripts/deploy.sh)"
# ─────────────────────────────────────────────────────────────

REPO="https://github.com/dsmrpha-rgb/automated-bot.git"
APP_DIR="/opt/automated-bot"
SERVICE_NAME="automated-bot"
PYTHON="python3"
VENV_DIR="$APP_DIR/venv"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { printf "${CYAN}[INFO]${NC}  %s\n" "$1"; }
ok()    { printf "${GREEN}[ OK ]${NC}  %s\n" "$1"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$1"; }
err()   { printf "${RED}[ERR ]${NC}  %s\n" "$1"; exit 1; }

# ── Pre-flight checks ───────────────────────────────────────
[ "$(id -u)" -ne 0 ] && err "Run this script as root (sudo)."

info "Updating system packages..."
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git curl > /dev/null
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip git curl > /dev/null
elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip git curl > /dev/null
else
    warn "Unknown package manager — make sure python3, pip, git are installed."
fi
ok "System packages ready."

# ── Clone or update the repo ────────────────────────────────
if [ -d "$APP_DIR/.git" ]; then
    info "Existing installation found. Pulling latest..."
    cd "$APP_DIR"
    git fetch --all
    git reset --hard origin/master
    ok "Updated to latest."
else
    info "Cloning repository..."
    rm -rf "$APP_DIR"
    git clone "$REPO" "$APP_DIR"
    ok "Cloned to $APP_DIR."
fi

cd "$APP_DIR"

# ── Python virtual environment ──────────────────────────────
info "Setting up Python virtual environment..."
$PYTHON -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r requirements.txt -q
ok "Python dependencies installed."

# ── Interactive .env setup ──────────────────────────────────
if [ -f "$APP_DIR/.env" ]; then
    warn ".env already exists. Skipping interactive setup."
    warn "Edit manually: nano $APP_DIR/.env"
else
    echo ""
    printf "${CYAN}══════════════════════════════════════════${NC}\n"
    printf "${CYAN}       Bot Configuration Setup${NC}\n"
    printf "${CYAN}══════════════════════════════════════════${NC}\n"
    echo ""

    printf "Enter bot tokens (comma-separated for multiple bots):\n"
    printf "${YELLOW}BOT_TOKENS=${NC}"
    read -r BOT_TOKENS

    printf "\nEnter admin Telegram user IDs (comma-separated):\n"
    printf "${YELLOW}ADMIN_IDS=${NC}"
    read -r ADMIN_IDS

    printf "\nEnter BTC wallet address (or leave empty):\n"
    printf "${YELLOW}BTC_WALLET=${NC}"
    read -r BTC_WALLET

    printf "\nEnter LTC wallet address (or leave empty):\n"
    printf "${YELLOW}LTC_WALLET=${NC}"
    read -r LTC_WALLET

    printf "\nEnter USDT (ERC-20) wallet address (or leave empty):\n"
    printf "${YELLOW}USDT_WALLET=${NC}"
    read -r USDT_WALLET

    cat > "$APP_DIR/.env" <<ENVEOF
BOT_TOKENS=$BOT_TOKENS
ADMIN_IDS=$ADMIN_IDS
BTC_WALLET=$BTC_WALLET
LTC_WALLET=$LTC_WALLET
USDT_WALLET=$USDT_WALLET
ENVEOF

    chmod 600 "$APP_DIR/.env"
    ok ".env created."
fi

# ── Systemd service ─────────────────────────────────────────
info "Creating systemd service..."

cat > /etc/systemd/system/${SERVICE_NAME}.service <<SVCEOF
[Unit]
Description=Mamahadesa Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/python bot.py
Restart=always
RestartSec=5
EnvironmentFile=$APP_DIR/.env

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
ok "Systemd service created and started."

# ── Done ────────────────────────────────────────────────────
echo ""
printf "${GREEN}══════════════════════════════════════════${NC}\n"
printf "${GREEN}       Deployment complete!${NC}\n"
printf "${GREEN}══════════════════════════════════════════${NC}\n"
echo ""
info "Useful commands:"
echo "  Status:   systemctl status $SERVICE_NAME"
echo "  Logs:     journalctl -u $SERVICE_NAME -f"
echo "  Restart:  systemctl restart $SERVICE_NAME"
echo "  Stop:     systemctl stop $SERVICE_NAME"
echo "  Config:   nano $APP_DIR/.env"
echo ""
