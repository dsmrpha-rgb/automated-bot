"""
Multi-bot registry for HADES PARFUMES.

Stores all Bot instances so that notifications, broadcasts,
and scheduled announcements can be sent from every bot.
"""

import os

from aiogram import Bot
from aiogram.types import FSInputFile

# All running Bot instances
_bots: list[Bot] = []

# Directory for downloaded photos (next to this file)
_PHOTO_DIR = os.path.join(os.path.dirname(__file__), "photos_cache")
os.makedirs(_PHOTO_DIR, exist_ok=True)


def register_bot(bot: Bot) -> None:
    """Add a Bot instance to the registry."""
    if bot not in _bots:
        _bots.append(bot)


def get_all_bots() -> list[Bot]:
    """Return all registered Bot instances."""
    return list(_bots)


async def download_photo(bot: Bot, file_id: str) -> str:
    """Download a photo from Telegram and return the local file path.

    This is needed because file_ids are bot-specific — a file_id obtained
    through one bot token cannot be used to send photos via another bot.
    """
    file = await bot.get_file(file_id)
    ext = os.path.splitext(file.file_path or "photo.jpg")[1] or ".jpg"
    local_path = os.path.join(_PHOTO_DIR, f"{file_id[:40]}{ext}")
    await bot.download_file(file.file_path, local_path)
    return local_path


async def send_message_all_bots(chat_id: int, **kwargs) -> None:
    """Send a message via ALL registered bots (best-effort)."""
    for bot in _bots:
        try:
            await bot.send_message(chat_id=chat_id, **kwargs)
        except Exception:
            pass


async def send_photo_all_bots(chat_id: int, photo: str, **kwargs) -> None:
    """Send a photo via ALL registered bots (best-effort).

    `photo` must be a local file path (not a Telegram file_id).
    Uses FSInputFile so every bot uploads its own copy.
    """
    for bot in _bots:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(photo),
                **kwargs,
            )
        except Exception:
            pass
