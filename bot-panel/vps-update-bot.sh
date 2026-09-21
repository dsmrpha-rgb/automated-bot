#!/bin/bash
# Update a single bot's CODE from git without touching its live menu_config.json
# or data.json. Usage: sudo bash vps-update-bot.sh <bot-name>
set -e
NAME="$1"
[ -z "$NAME" ] && { echo "usage: $0 <bot-name>   (folder name under /opt)"; exit 1; }

DIR="/opt/$NAME"
SVC="bot-$NAME"

[ -d "$DIR/.git" ] || { echo "no git repo at $DIR"; exit 1; }

echo "==> fetching..."
cd "$DIR"
git fetch origin

echo "==> updating code files only (menu_config.json + data.json preserved)..."
# checkout every tracked .py from origin/master; leaves .json (menu/data) alone
git checkout origin/master -- '*.py'

if [ -x "$DIR/venv/bin/pip" ] && [ -f "$DIR/requirements.txt" ]; then
    echo "==> refreshing deps..."
    "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt" -q || true
fi

echo "==> restarting $SVC..."
systemctl restart "$SVC"
sleep 1
systemctl --no-pager --lines=0 status "$SVC" | head -3
echo "==> done. Menu & data untouched."
