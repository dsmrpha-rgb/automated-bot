import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import BOT_TOKENS
from handlers import router
from admin_handlers import router as admin_router, start_scheduler
from data_store import seed_from_texts
from deposit_monitor import start_deposit_monitor
import bot_registry


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Seed data.json from hardcoded texts.py if empty (first run migration)
    seed_from_texts()

    # Create all bot instances and verify each one
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
        logging.error("No valid bots! Check your BOT_TOKENS in .env")
        return

    logging.info("Starting polling for %d bot(s)...", len(bots))

    dp = Dispatcher()
    dp.include_router(admin_router)  # admin first so its callbacks take priority
    dp.include_router(router)

    # Start background tasks (use first bot as the primary for scheduler/monitor)
    start_scheduler(bots[0])
    start_deposit_monitor(bots[0])

    # Delete webhooks for all bots
    for bot in bots:
        await bot.delete_webhook(drop_pending_updates=True)

    # Start polling for all bots with the same dispatcher
    await dp.start_polling(*bots)


if __name__ == "__main__":
    asyncio.run(main())
