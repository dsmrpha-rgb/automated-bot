# Bot-Shop Constructor — integration

Two sides. The **panel** files go into your `bot-panel` (this folder). The
three **engine** files in `_bot_engine/` go into the **automated-bot** repo
(github.com/dsmrpha-rgb/automated-bot) so new bots clone them.

## 1. Panel side (already in this folder)

Changed / new files:
- `app.py`            — registers the constructor blueprint; "Deploy New Bot"
                         now has a "Config-driven bot" checkbox → runs
                         `bot_engine.py` and seeds a `menu_config.json`.
- `constructor.py`    — NEW. The constructor backend (blueprint).
- `templates/constructor.html` — NEW. The visual builder UI.
- `templates/dashboard.html`   — adds the 🧩 Constructor button per bot.
- `templates/add_bot.html`     — adds the engine checkbox.

Deploy: push `bot-panel` to its repo, then on the VPS Git-Pull the `panel`
bot from the dashboard and Restart it. No new Python deps (werkzeug ships
with Flask).

## 2. Engine side → automated-bot repo

Copy these into the root of the `automated-bot` repo and push:
- `menu_engine.py`   — the config-driven menu engine (renders pages from JSON).
- `bot_engine.py`    — entry point for constructor bots (reuses your admin
                       panel + crypto/deposit/QR backbone unchanged).
- `menu_config.json` — the editable menu (seeded to reproduce your current
                       Hades main menu; edit it from the Constructor).

**Your live Mama Hades bot is NOT affected**: it keeps running `bot.py`.
`bot_engine.py` is only used by bots you deploy with the engine checkbox on
(or by pointing a service's ExecStart at it).

## How it works

- The user-facing menu is 100% driven by `menu_config.json`.
- The engine OWNS navigation: the /start screen and every custom `page:*`.
- Dynamic leaves (product list, district picker, crypto payment, balance,
  deposits, language) are REUSED from your existing handlers.py — payment
  flow is unchanged.
- Button actions: `page:<id>` (go to another page), `builtin:<key>`
  (products / balance / language), `url:<link>`, or `noop`.
- Placeholders in captions: `{balance} {bot_username} {user_id}
  {referrals} {earned}`.
- The admin panel (📦 products, 🗺 districts, 📢 broadcast, ✉ private,
  📅 scheduled, 👥 users, 📊 stats) ships as-is via admin_handlers.py —
  toggle its button per page with "Show admin-panel button".

## Editing a bot's appearance anytime

Dashboard → 🧩 Constructor → edit pages/buttons/text/images or tokens/wallets
→ **Save & Restart**. The bot re-reads `menu_config.json` on restart.
(The engine also re-reads the file on every tap, so most text/button edits
show up even without a restart — restart is needed for token/wallet changes.)
