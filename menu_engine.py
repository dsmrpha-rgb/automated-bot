"""
Config-driven flow engine.

Turns menu_config.json into the ENTIRE user-facing bot: pages, captions
(multi-language), images, inline-button layouts, multi-step input flows and
dynamic crypto top-up receipts — all editable from the Bot Panel constructor
without touching code.

Page types
----------
  message  (default) — image + caption + buttons.
  input    — prompts the user, waits for a text reply, validates it, stores it
             in a session variable, then navigates to `input.next`.
  receipt  — computes a crypto payment (wallet by chosen coin, amount by live
             rate, request id, QR) and renders a fully templated caption.

Button actions (stored per button)
----------------------------------
  page:<id>                       navigate to a config page
  choose:<var>:<value>:<page>     set a session var, then navigate
  confirm:<page>                  ping admins ("user paid"), then navigate
  builtin:<key>                   legacy delegate (products/balance/language)
  url:<https-url>                 open a URL
  noop                            do nothing

Templating: captions and labels are `.format`-ed with the user's session
variables plus {balance} {bot_username} {user_id} {referrals} {earned}.
"""
from __future__ import annotations

import json
import os
import random
import re
from typing import Any

from aiogram import F, Router
from aiogram.filters import CommandStart, BaseFilter
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

# per-user session state: {user_id: {"vars": {...}, "await": {input cfg} | None}}
SESSIONS: dict[int, dict] = {}

BUILTINS: dict[str, str] = {
    "products": "city:tbilisi",
    "balance": "menu:balance",
    "language": "menu:language",
}

ADMIN_BUTTON_TEXT = "⚙ ადმინ პანელი"
ADMIN_BUTTON_CB = "admin:menu"

# emoji cues used to fake button "colors" (Bot API has no real per-button color)
STYLE_PREFIX = {"green": "🟢 ", "red": "🔴 ", "check": "✅ ", "cross": "❌ "}


# ── config ───────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG: dict[str, Any] = {
    "version": 2, "start_page": "main", "default_lang": "ka", "langs": ["ka", "ru", "en"],
    "pages": {"main": {"type": "message", "image": "", "parse_mode": "HTML",
                       "show_admin_button": True,
                       "text": {"ka": "მთავარი მენიუ"}, "rows": []}},
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


def _sess(uid: int) -> dict:
    return SESSIONS.setdefault(uid, {"vars": {}, "await": None})


def _lang(uid: int, cfg: dict) -> str:
    try:
        return ds.get_user_lang(uid)
    except Exception:
        return cfg.get("default_lang", "ka")


def _pick(field: Any, lang: str, default_lang: str) -> str:
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        return field.get(lang) or field.get(default_lang) or next(iter(field.values()), "")
    return ""


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _fmt(text: str, ctx: dict) -> str:
    try:
        return text.format_map(_SafeDict(ctx))
    except Exception:
        return text


def _image_path(image: str) -> str | None:
    if not image:
        return None
    p = image if os.path.isabs(image) else os.path.join(_BASE_DIR, image)
    return p if os.path.exists(p) else None


async def _context(uid: int, bot) -> dict:
    ctx: dict[str, Any] = dict(_sess(uid)["vars"])
    ctx["user_id"] = uid
    try:
        ctx.setdefault("balance", ds.get_user_balance(uid))
    except Exception:
        ctx.setdefault("balance", 0.0)
    try:
        me = await bot.get_me()
        ctx.setdefault("bot_username", me.username or "")
    except Exception:
        ctx.setdefault("bot_username", "")
    ctx.setdefault("referrals", 0)
    ctx.setdefault("earned", 0.0)
    return ctx


def _label(btn: dict, lang: str, default_lang: str, ctx: dict) -> str:
    text = _fmt(_pick(btn.get("label", ""), lang, default_lang), ctx)
    style = btn.get("style")
    if style in STYLE_PREFIX:
        text = STYLE_PREFIX[style] + text
    return text


def _keyboard(page: dict, lang: str, default_lang: str, ctx: dict, uid: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in page.get("rows", []):
        built: list[InlineKeyboardButton] = []
        for btn in row:
            label = _label(btn, lang, default_lang, ctx)
            action = str(btn.get("action", "noop"))
            if action.startswith("url:"):
                built.append(InlineKeyboardButton(text=label, url=action[4:]))
            elif action.startswith("builtin:"):
                built.append(InlineKeyboardButton(text=label, callback_data=BUILTINS.get(action[8:], "noop")))
            elif action.startswith("page:") or action.startswith("choose:") or action.startswith("confirm:"):
                built.append(InlineKeyboardButton(text=label, callback_data=action))
            else:
                built.append(InlineKeyboardButton(text=label, callback_data="noop"))
        if built:
            rows.append(built)
    if page.get("show_admin_button") and uid in ADMIN_IDS:
        rows.append([InlineKeyboardButton(text=ADMIN_BUTTON_TEXT, callback_data=ADMIN_BUTTON_CB)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── receipt computation ──────────────────────────────────────────────────────
async def _compute_receipt(page: dict, uid: int) -> tuple[str | None, dict]:
    """Fill session vars for a receipt page. Returns (qr_path_or_None, ctx)."""
    from config import CRYPTO_WALLETS
    from crypto_rates import get_usd_to_crypto
    from qr_generator import generate_qr

    rc = page.get("receipt", {}) or {}
    v = _sess(uid)["vars"]

    coin = (v.get(rc.get("coin_var", "crypto")) or rc.get("coin_fixed") or "BTC").upper()
    amount = float(v.get(rc.get("amount_var", "amount"), 0) or 0)
    fiat_to_usd = float(rc.get("fiat_to_usd", 1.0))
    usd_amount = round(amount * fiat_to_usd, 2)

    wallet = CRYPTO_WALLETS.get(coin, "")
    try:
        crypto_amount = await get_usd_to_crypto(coin, usd_amount)
    except Exception:
        crypto_amount = 0.0

    rid_var = rc.get("request_id_var", "request_id")
    if not v.get(rid_var):
        v[rid_var] = random.randint(100000, 999999)

    v.update({
        "coin": coin, "wallet": wallet, "crypto_amount": crypto_amount,
        "usd_amount": usd_amount, "amount": amount, "request_id": v[rid_var],
    })

    qr_path = None
    if rc.get("use_qr", True) and wallet:
        try:
            qr_path = generate_qr(wallet, coin)
        except Exception:
            qr_path = None
    return qr_path, dict(v)


# ── rendering ────────────────────────────────────────────────────────────────
async def render_page(target, page_id: str, *, is_start: bool = False) -> None:
    cfg = load_config()
    pages = cfg.get("pages", {})
    default_lang = cfg.get("default_lang", "ka")
    if page_id not in pages:
        page_id = cfg.get("start_page", "main")
    page = pages.get(page_id, {})

    if isinstance(target, CallbackQuery):
        message, uid, bot = target.message, target.from_user.id, target.bot
    else:
        message, uid, bot = target, target.from_user.id, target.bot

    lang = _lang(uid, cfg)
    ptype = page.get("type", "message")

    # input page: arm the awaiting-input state, then render its prompt as a message
    if ptype == "input":
        inp = page.get("input", {}) or {}
        _sess(uid)["await"] = {"page": page_id, "cfg": inp}

    # receipt page: compute dynamic values, use QR as image
    qr_override = None
    if ptype == "receipt":
        qr_override, _ = await _compute_receipt(page, uid)

    ctx = await _context(uid, bot)
    caption = _fmt(_pick(page.get("text", ""), lang, default_lang), ctx)
    parse_mode = page.get("parse_mode") or None
    kb = _keyboard(page, lang, default_lang, ctx, uid)
    img = qr_override or _image_path(page.get("image", ""))

    await _emit(message, caption, parse_mode, kb, img, is_start=is_start)


async def _emit(message, caption, parse_mode, kb, img, *, is_start=False):
    if is_start:
        if img:
            await message.answer_photo(photo=FSInputFile(img), caption=caption,
                                       parse_mode=parse_mode, reply_markup=kb)
        else:
            await message.answer(text=caption or " ", parse_mode=parse_mode, reply_markup=kb)
        return
    try:
        if img and message.photo:
            await message.edit_media(media=InputMediaPhoto(media=FSInputFile(img),
                                     caption=caption, parse_mode=parse_mode), reply_markup=kb)
            return
        if not img and not message.photo:
            await message.edit_text(text=caption or " ", parse_mode=parse_mode, reply_markup=kb)
            return
    except Exception:
        pass
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
    try:
        ds.register_user(message.from_user.id, message.from_user.username or "")
    except Exception:
        pass
    _sess(message.from_user.id)["await"] = None  # reset any pending input
    await render_page(message, cfg.get("start_page", "main"), is_start=True)


@engine_router.callback_query(F.data == "menu:main")
async def engine_back_main(callback: CallbackQuery) -> None:
    cfg = load_config()
    _sess(callback.from_user.id)["await"] = None
    await render_page(callback, cfg.get("start_page", "main"))
    await callback.answer()


@engine_router.callback_query(F.data.startswith("page:"))
async def engine_navigate(callback: CallbackQuery) -> None:
    await render_page(callback, callback.data.split(":", 1)[1])
    await callback.answer()


@engine_router.callback_query(F.data.startswith("choose:"))
async def engine_choose(callback: CallbackQuery) -> None:
    # choose:<var>:<value>:<page>
    try:
        _, var, value, page = callback.data.split(":", 3)
    except ValueError:
        await callback.answer(); return
    _sess(callback.from_user.id)["vars"][var] = value
    await render_page(callback, page)
    await callback.answer()


@engine_router.callback_query(F.data.startswith("confirm:"))
async def engine_confirm(callback: CallbackQuery) -> None:
    # confirm:<page> — ping admins that the user says they've paid, then navigate
    page = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    v = _sess(uid)["vars"]
    un = f"@{callback.from_user.username}" if callback.from_user.username else "no-username"
    text = (f"💸 User {un} (id {uid}) pressed 'I paid'.\n"
            f"Request #{v.get('request_id','?')} · {v.get('crypto_amount','?')} "
            f"{v.get('coin','?')} · amount {v.get('amount','?')}")
    for admin_id in ADMIN_IDS:
        try:
            await callback.bot.send_message(admin_id, text)
        except Exception:
            pass
    await render_page(callback, page)
    await callback.answer()


# ── input handling ───────────────────────────────────────────────────────────
class AwaitingInput(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        s = SESSIONS.get(message.from_user.id)
        return bool(s and s.get("await"))


@engine_router.message(AwaitingInput())
async def engine_input(message: Message) -> None:
    s = _sess(message.from_user.id)
    await_cfg = s.get("await") or {}
    inp = await_cfg.get("cfg", {})
    cfg = load_config()
    lang = _lang(message.from_user.id, cfg)
    default_lang = cfg.get("default_lang", "ka")

    raw = (message.text or "").strip()
    kind = inp.get("kind", "text")
    ok = True
    value: Any = raw

    if kind == "number":
        try:
            value = float(raw.replace(",", "."))
            if value == int(value):
                value = int(value)
            lo = inp.get("min"); hi = inp.get("max")
            if lo is not None and value < float(lo):
                ok = False
            if hi is not None and value > float(hi):
                ok = False
        except ValueError:
            ok = False
    else:
        rgx = inp.get("regex")
        if rgx:
            try:
                if not re.match(rgx, raw):
                    ok = False
            except re.error:
                ok = True

    if not ok:
        err = _pick(inp.get("error", "❌ Invalid value, try again."), lang, default_lang)
        await message.answer(err or "❌ Invalid value, try again.")
        return

    # store and advance
    s["vars"][inp.get("var", "value")] = value
    s["await"] = None
    nxt = inp.get("next") or cfg.get("start_page", "main")
    await render_page(message, nxt, is_start=True)
