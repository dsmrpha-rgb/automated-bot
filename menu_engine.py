"""
Config-driven menu engine.

Turns a JSON file (menu_config.json) into the entire user-facing menu of a
Telegram shop bot: pages, captions (multi-language), images and inline-button
layouts — all editable from the Bot Panel constructor without touching code.

The engine OWNS navigation (the start screen and every custom `page:*`).
Dynamic leaf behaviours (product list, district picker, crypto payment, balance,
deposits, language switch) are REUSED as-is from the existing handlers.py /
keyboards.py so nothing about the payment flow changes.

Button action grammar (stored in menu_config.json):
    page:<id>        -> navigate to another config page (engine renders it)
    builtin:<key>    -> hand off to a built-in dynamic screen (see BUILTINS)
    url:<https-url>  -> open a URL
    noop             -> do nothing (visual separator / label)

This module registers a single Router (`engine_router`). bot_engine.py includes
it together with admin_handlers and the reused leaf handlers.
"""
from __future__ import annotations

import json
import os
from typing import Any

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

import data_store as ds
from config import ADMIN_IDS

_BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(_BASE_DIR, "menu_config.json")

engine_router = Router(name="menu_engine")


# ── builtin action key -> the legacy callback_data that already has a handler ──
# These map a constructor "builtin" button to the callback the reused handlers
# in handlers.py already listen for. Add to this as you expose more builtins.
BUILTINS: dict[str, str] = {
    "products": "city:tbilisi",   # show the product listing
    "balance": "menu:balance",    # balance screen with deposit options
    "language": "menu:language",  # language picker
}

# Admin button (appended to any page whose "show_admin_button" is true)
ADMIN_BUTTON_TEXT = "⚙ ადმინ პანელი"
ADMIN_BUTTON_CB = "admin:menu"


# ── config loading (re-read every render so edits apply on next tap) ──────────

_DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "start_page": "main",
    "default_lang": "ka",
    "langs": ["ka", "ru", "en"],
    "pages": {
        "main": {
            "image": "menu.jpg",
            "text": {"ka": "მთავარი მენიუ"},
            "parse_mode": "HTML",
            "show_admin_button": True,
            "rows": [],
        }
    },
}


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return _DEFAULT_CONFIG
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if "pages" not in cfg or "start_page" not in cfg:
            return _DEFAULT_CONFIG
        return cfg
    except Exception:
        return _DEFAULT_CONFIG


def _lang(user_id: int, cfg: dict) -> str:
    try:
        return ds.get_user_lang(user_id)
    except Exception:
        return cfg.get("default_lang", "ka")


def _pick_text(field: Any, lang: str, default_lang: str) -> str:
    """A text field may be a plain string or {lang: string}. Resolve it."""
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        return field.get(lang) or field.get(default_lang) or next(iter(field.values()), "")
    return ""


class _SafeDict(dict):
    """str.format_map helper: leave unknown {placeholders} untouched."""
    def __missing__(self, key):
        return "{" + key + "}"


def _render_vars(text: str, ctx: dict) -> str:
    try:
        return text.format_map(_SafeDict(ctx))
    except Exception:
        return text


def _image_path(image: str) -> str | None:
    if not image:
        return None
    path = image if os.path.isabs(image) else os.path.join(_BASE_DIR, image)
    return path if os.path.exists(path) else None


def _build_keyboard(page: dict, lang: str, default_lang: str,
                    ctx: dict, user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in page.get("rows", []):
        built_row: list[InlineKeyboardButton] = []
        for btn in row:
            label = _render_vars(
                _pick_text(btn.get("label", ""), lang, default_lang), ctx
            )
            action = str(btn.get("action", "noop"))
            if action.startswith("url:"):
                built_row.append(InlineKeyboardButton(text=label, url=action[4:]))
            elif action.startswith("builtin:"):
                cb = BUILTINS.get(action[8:], "noop")
                built_row.append(InlineKeyboardButton(text=label, callback_data=cb))
            elif action.startswith("page:"):
                built_row.append(InlineKeyboardButton(text=label, callback_data=action))
            else:
                built_row.append(InlineKeyboardButton(text=label, callback_data="noop"))
        if built_row:
            rows.append(built_row)
    if page.get("show_admin_button") and user_id in ADMIN_IDS:
        rows.append([InlineKeyboardButton(text=ADMIN_BUTTON_TEXT, callback_data=ADMIN_BUTTON_CB)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _user_context(user_id: int, bot) -> dict:
    """Variables available for {placeholder} substitution in captions/labels."""
    ctx: dict[str, Any] = {"user_id": user_id}
    try:
        ctx["balance"] = ds.get_user_balance(user_id)
    except Exception:
        ctx["balance"] = 0.0
    try:
        me = await bot.get_me()
        ctx["bot_username"] = me.username or ""
    except Exception:
        ctx["bot_username"] = ""
    # common extras used by referral-style pages
    ctx.setdefault("referrals", 0)
    ctx.setdefault("earned", 0.0)
    return ctx


async def render_page(target, page_id: str, *, is_start: bool = False) -> None:
    """
    Render a config page onto `target` (a Message for /start, or a
    CallbackQuery for navigation).
    """
    cfg = load_config()
    pages = cfg.get("pages", {})
    default_lang = cfg.get("default_lang", "ka")

    if page_id not in pages:
        page_id = cfg.get("start_page", "main")
    page = pages.get(page_id, {})

    if isinstance(target, CallbackQuery):
        message = target.message
        user_id = target.from_user.id
        bot = target.bot
    else:  # Message (/start)
        message = target
        user_id = target.from_user.id
        bot = target.bot

    lang = _lang(user_id, cfg)
    ctx = await _user_context(user_id, bot)

    caption = _render_vars(_pick_text(page.get("text", ""), lang, default_lang), ctx)
    parse_mode = page.get("parse_mode") or None
    kb = _build_keyboard(page, lang, default_lang, ctx, user_id)
    img = _image_path(page.get("image", ""))

    # /start always sends a fresh message
    if is_start:
        if img:
            await message.answer_photo(photo=FSInputFile(img), caption=caption,
                                       parse_mode=parse_mode, reply_markup=kb)
        else:
            await message.answer(text=caption or " ", parse_mode=parse_mode, reply_markup=kb)
        return

    # navigation: try to edit in place, else delete + resend
    try:
        if img and message.photo:
            await message.edit_media(
                media=InputMediaPhoto(media=FSInputFile(img), caption=caption,
                                      parse_mode=parse_mode),
                reply_markup=kb,
            )
            return
        if not img and not message.photo:
            await message.edit_text(text=caption or " ", parse_mode=parse_mode,
                                    reply_markup=kb)
            return
    except Exception:
        pass

    # media type changed (photo<->text) or edit failed: replace the message
    try:
        await message.delete()
    except Exception:
        pass
    if img:
        await message.answer_photo(photo=FSInputFile(img), caption=caption,
                                   parse_mode=parse_mode, reply_markup=kb)
    else:
        await message.answer(text=caption or " ", parse_mode=parse_mode, reply_markup=kb)


# ── routes ────────────────────────────────────────────────────────────────────

@engine_router.message(CommandStart())
async def engine_start(message: Message) -> None:
    cfg = load_config()
    # register the user (reuse existing behaviour)
    try:
        ds.register_user(message.from_user.id, message.from_user.username or "")
    except Exception:
        pass
    await render_page(message, cfg.get("start_page", "main"), is_start=True)


@engine_router.callback_query(F.data == "menu:main")
async def engine_back_main(callback: CallbackQuery) -> None:
    cfg = load_config()
    await render_page(callback, cfg.get("start_page", "main"))
    await callback.answer()


@engine_router.callback_query(F.data.startswith("page:"))
async def engine_navigate(callback: CallbackQuery) -> None:
    page_id = callback.data.split(":", 1)[1]
    await render_page(callback, page_id)
    await callback.answer()
