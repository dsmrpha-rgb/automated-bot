"""FSM states for all admin multi-step flows."""

from aiogram.filters.state import StatesGroup, State


class AddProductFSM(StatesGroup):
    """Add a new product: name -> price -> description -> quantity."""
    waiting_name = State()
    waiting_price = State()
    waiting_description = State()
    waiting_quantity = State()


class EditProductFSM(StatesGroup):
    """Edit an existing product field."""
    waiting_field = State()      # which field to edit
    waiting_new_value = State()  # the new value


class AddDistrictFSM(StatesGroup):
    """Add a new district: name -> image filename."""
    waiting_name = State()
    waiting_image = State()


class BroadcastFSM(StatesGroup):
    """Send an immediate announcement (text, optional photo)."""
    waiting_message = State()
    waiting_photo = State()      # optional photo attachment
    waiting_confirm = State()


class ScheduledAnnouncementFSM(StatesGroup):
    """Schedule an announcement for a future time."""
    waiting_message = State()
    waiting_photo = State()
    waiting_datetime = State()   # e.g. "2025-06-15 14:00"
    waiting_recurring = State()  # none / daily / weekly / monthly


class BanUserFSM(StatesGroup):
    """Ban or unban a user by Telegram ID."""
    waiting_user_id = State()


class SetBalanceFSM(StatesGroup):
    """Set a user's balance by Telegram ID."""
    waiting_user_id = State()
    waiting_amount = State()
