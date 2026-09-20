"""
Entry point for CONSTRUCTOR-BUILT bots (config-driven menu).

Runs the exact same payment / admin / crypto backbone as bot.py, but the
user-facing menu is driven entirely by menu_config.json via menu_engine.py
instead of the hardcoded handlers.

Deploy uses this file as the systemd ExecStart (…/venv/bin/python bot_engine.py)
so the legacy bot.py and its bots are never affected.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import BotCommand

from config import BOT_TOKENS
from data_store import seed_from_texts
from deposit_monitor import start_deposit_monitor
from admin_handlers import router as admin_router, start_scheduler
from menu_engine import engine_router
import bot_registry

# Reuse the dynamic leaf handlers from handlers.py WITHOUT their navigation
# handlers (start / back / info screens) — the engine owns navigation.
import handlers as H

leaf_router = Router(name="leaf_handlers")
leaf_router.callback_query.register(H.on_city_tbilisi, F.data == "city:tbilisi")
leaf_router.callback_query.register(H.on_product_selected, F.data.startswith("product:"))
leaf_router.callback_query.register(H.on_district_selected, F.data.startswith("district:"))
leaf_router.callback_query.register(H.on_crypto_payment, F.data.startswith("pay:"))
leaf_router.callback_query.register(H.on_balance, F.data == "menu:balance")
leaf_router.callback_query.register(H.on_deposit_crypto, F.data.startswith("deposit:"))
leaf_router.callback_query.register(H.on_language, F.data == "menu:language")
leaf_router.callback_query.register(H.on_language_selected, F.data.startswith("lang:"))
leaf_router.callback_query.register(H.on_noop, F.data == "noop")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    seed_from_texts()

    bots = []
    for i, token in enumerate(BOT_TOKENS, 1):
        bot = Bot(token=token)
        try:
            me = await bot.get_me()
            logging.info("Bot %d OK: @%s (id=%s)", i, me.username, me.id)
        except Exception as e:
            logging.error("Bot %d FAILED (token=%s...): %s", i, token[:8], e)
            continue
        bot_registry.register_bot(bot)
        bots.append(bot)

    if not bots:
        logging.error("No valid bots! Check BOT_TOKENS in .env")
        return

    logging.info("Starting polling for %d bot(s) [engine mode]...", len(bots))

    dp = Dispatcher()
    dp.include_router(admin_router)   # admin callbacks first
    dp.include_router(engine_router)  # config-driven navigation
    dp.include_router(leaf_router)    # reused dynamic leaves

    start_scheduler(bots[0])
    start_deposit_monitor(bots[0])

    commands = [BotCommand(command="start", description="На главную")]
    for bot in bots:
        await bot.set_my_commands(commands)
        await bot.delete_webhook(drop_pending_updates=True)

    await dp.start_polling(*bots)


if __name__ == "__main__":
    asyncio.run(main())
