#!/bin/bash
# Update the Bot Panel on the VPS from git. Keeps /opt/bot-panel/.env.
set -e
REPO=/opt/automated-bot-repo
PANEL=/opt/bot-panel

[ -d "$REPO/.git" ] || { echo "repo not found at $REPO"; exit 1; }

echo "==> pulling repo..."
cd "$REPO"
git fetch --all
git reset --hard origin/master

echo "==> syncing panel files..."
cp "$REPO"/bot-panel/*.py            "$PANEL"/
cp "$REPO"/bot-panel/templates/*.html "$PANEL"/templates/
cp "$REPO"/bot-panel/requirements.txt "$PANEL"/requirements.txt

if [ -x "$PANEL/venv/bin/pip" ]; then
    echo "==> refreshing deps..."
    "$PANEL/venv/bin/pip" install -r "$PANEL/requirements.txt" -q || true
fi

echo "==> restarting panel..."
systemctl restart bot-panel
sleep 1
systemctl --no-pager --lines=0 status bot-panel | head -3
echo "==> done."
