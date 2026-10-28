"""Outer middleware that enforces the `banned` flag stored in data.json."""
import data_store as ds
from config import ADMIN_IDS

async def ban_gate(handler, event, data):
    uid = getattr(getattr(event, "from_user", None), "id", None)
    if uid is not None and uid not in ADMIN_IDS and ds.is_user_banned(uid):
        if hasattr(event, "data"):          # CallbackQuery: stop the loading spinner, show nothing
            try:
                await event.answer()
            except Exception:
                pass
        return None                         # silent: banned users get no reply at all
    return await handler(event, data)


def install(dp) -> None:
    dp.message.outer_middleware(ban_gate)
    dp.callback_query.outer_middleware(ban_gate)
