"""Admin notifications with per-admin switches (menu_config.json -> notifications).

notifications: {"<admin id>": {"<event>": false}}  - anything not switched off stays ON,
so bots without the section behave exactly as before. Set in Global Admin.
"""
import logging

import settings_store
from config import ADMIN_IDS

EVENTS = {
    "new_user": "New user started the bot",
    "topup_started": "Top-up initiated (user opened a deposit address)",
    "deposit_received": "Deposit detected on the wallet",
    "purchase": "Product bought with balance",
    "paid_pressed": "User pressed “I paid”",
    "site_user": "New website user registered",
}


def enabled(admin_id: int, event: str) -> bool:
    sec = (settings_store.cfg().get("notifications") or {}).get(str(admin_id))
    return not (isinstance(sec, dict) and sec.get(event) is False)


def recipients(event: str) -> list[int]:
    return [a for a in ADMIN_IDS if enabled(a, event)]


async def send(bot, event: str, text: str, **kw) -> None:
    for admin_id in recipients(event):
        try:
            await bot.send_message(admin_id, text, **kw)
        except Exception as e:  # noqa: BLE001
            logging.warning("notify %s -> %s failed: %s", event, admin_id, e)
