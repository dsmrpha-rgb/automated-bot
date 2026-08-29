"""
Multi-bot registry for HADES PARFUMES.

Stores all Bot instances so that notifications, broadcasts,
and scheduled announcements can be sent from every bot.
"""

from aiogram import Bot

# All running Bot instances
_bots: list[Bot] = []


def register_bot(bot: Bot) -> None:
    """Add a Bot instance to the registry."""
    if bot not in _bots:
        _bots.append(bot)


def get_all_bots() -> list[Bot]:
    """Return all registered Bot instances."""
    return list(_bots)


async def send_message_all_bots(chat_id: int, **kwargs) -> None:
    """Send a message via ALL registered bots (best-effort)."""
    for bot in _bots:
        try:
            await bot.send_message(chat_id=chat_id, **kwargs)
        except Exception:
            pass


async def send_photo_all_bots(chat_id: int, **kwargs) -> None:
    """Send a photo via ALL registered bots (best-effort)."""
    for bot in _bots:
        try:
            await bot.send_photo(chat_id=chat_id, **kwargs)
        except Exception:
            pass
