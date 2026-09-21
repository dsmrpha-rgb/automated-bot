# Ops cheat-sheet — bot-panel + automated-bot

Facts:
- GitHub repo: `github.com/dsmrpha-rgb/automated-bot`, branch **master**
- VPS repo clone: `/opt/automated-bot-repo`   (safe to hard-reset — nothing edited here)
- Panel runtime: `/opt/bot-panel`  (COPY of `bot-panel/` from the repo), service `bot-panel`
- Each bot: `/opt/<name>`, service `bot-<name>` (engine bots run `bot_engine.py`)
- A bot's LIVE menu lives in `/opt/<name>/menu_config.json`; its data in `data.json`.
  These must NOT be overwritten on code updates.

---

## 1. Push from Windows

From the panel folder:
```powershell
cd C:\Users\dsmrp\Desktop\mamahadesa\bot-panel
powershell -ExecutionPolicy Bypass -File .\git-push.ps1
```
`git-push.ps1` pushes panel changes + engine files (`menu_engine.py`, `bot_engine.py`,
`menu_config.json`) to automated-bot.

Manual equivalent (if you edited files by hand):
```powershell
cd C:\Users\dsmrp\Desktop\mamahadesa\bot-panel
git add -A
git commit -m "your message"
git push
```

---

## 2. Update the PANEL on the VPS

Option A — helper script (copy vps-update-panel.sh to the VPS once):
```bash
sudo bash /opt/vps-update-panel.sh
```

Option B — manual:
```bash
cd /opt/automated-bot-repo
git fetch --all && git reset --hard origin/master
cp bot-panel/*.py            /opt/bot-panel/
cp bot-panel/templates/*.html /opt/bot-panel/templates/
cp bot-panel/requirements.txt /opt/bot-panel/requirements.txt
/opt/bot-panel/venv/bin/pip install -r /opt/bot-panel/requirements.txt -q
systemctl restart bot-panel
```

Option C — full re-run of the installer (also keeps your .env):
```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/dsmrpha-rgb/automated-bot/master/bot-panel/deploy-panel.sh)"
```

---

## 3. Update a BOT's code on the VPS (keeps its menu + data)

Helper script (copy vps-update-bot.sh to the VPS once):
```bash
sudo bash /opt/vps-update-bot.sh <bot-name>
```

Manual:
```bash
cd /opt/<bot-name>
git fetch origin
git checkout origin/master -- '*.py'     # code only — menu_config.json & data.json untouched
[ -x venv/bin/pip ] && venv/bin/pip install -r requirements.txt -q
systemctl restart bot-<bot-name>
```

> Do NOT run `git reset --hard` in a bot dir — it wipes the bot's live menu_config.json.
> Use the constructor to change the menu; use this only to pull engine/code fixes.

---

## 4. Everyday systemd ops

```bash
systemctl status  bot-panel            # or bot-<name>
systemctl restart bot-panel
systemctl stop    bot-<name>
systemctl start   bot-<name>
journalctl -u bot-panel   -n 80 --no-pager      # last 80 log lines
journalctl -u bot-<name>  -f                     # live logs (Ctrl-C to stop)
```

List registered bots the panel knows:
```bash
cat /etc/bot-panel/bots.json
```

List all bot services:
```bash
systemctl list-units 'bot-*' --type=service
```

---

## 5. Typical release loop

1. Edit locally → `powershell -ExecutionPolicy Bypass -File .\git-push.ps1`
2. Panel changed?  → `sudo bash /opt/vps-update-panel.sh`
3. Engine/bot code changed? → `sudo bash /opt/vps-update-bot.sh <name>` for each bot
4. Menu/appearance change? → do it in the **Constructor** (no SSH needed), Save & Restart.
