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
  admin                           open the admin menu (admins only; hidden for others)
  noop                            do nothing

Page hooks (page["on_enter"], all optional) run when a user reaches the page:
  set_vars      {var: value}                 store session variables first
  notify_admins "template"                   message every admin ({vars} allowed,
                                             plus {username} {user_id})
  send          [{text, image}, ...]         extra messages sent to the user

Media: a page/hook `image` may be a photo (.jpg .png .webp), a GIF (.gif, sent as
an animation) or a video (.mp4 .mov .webm .m4v) — the extension decides.

Channel gate (config["gate"], optional): /start first asks the user to join a
channel; mode "verify" really checks membership (bot must be admin in the
channel), mode "dummy" lets anyone through after pressing the check button.

History: a `history` page (or any caption containing {purchases}) lists the
user's purchases, which admins record in the bot or in the Global Admin.

Templating: captions and labels are `.format`-ed with the user's session
variables plus {balance} {bot_username} {user_id} {referrals} {earned}.
"""
from __future__ import annotations

import html
import json
import os
import random
import re
from typing import Any

from aiogram import F, Router
from aiogram import BaseMiddleware
from aiogram.filters import CommandStart, BaseFilter
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
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


_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}


def _media_kind(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gif":
        return "animation"
    if ext in _VIDEO_EXTS:
        return "video"
    return "photo"


def _input_media(kind: str, path: str, caption: str, parse_mode):
    cls = {"video": InputMediaVideo, "animation": InputMediaAnimation}.get(kind, InputMediaPhoto)
    kw = {"supports_streaming": True} if kind == "video" else {}
    return cls(media=FSInputFile(path), caption=caption, parse_mode=parse_mode, **kw)


async def _send_media(sender, kind: str, path: str, caption, parse_mode, kb=None):
    """sender is a Message (answer_*) — returns the sent message."""
    if kind == "video":
        return await sender.answer_video(video=FSInputFile(path), caption=caption,
                                         parse_mode=parse_mode, reply_markup=kb, supports_streaming=True)
    if kind == "animation":
        return await sender.answer_animation(animation=FSInputFile(path), caption=caption,
                                             parse_mode=parse_mode, reply_markup=kb)
    return await sender.answer_photo(photo=FSInputFile(path), caption=caption,
                                     parse_mode=parse_mode, reply_markup=kb)


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
            elif action == "admin":
                if uid in ADMIN_IDS:
                    built.append(InlineKeyboardButton(text=label, callback_data=ADMIN_BUTTON_CB))
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


# ── catalog (products / districts) ───────────────────────────────────────────
def _list_items(source: str, city: str, uid: int | None = None) -> dict:
    """Return {slug: item_dict} for a data-driven list (a user's private listing replaces the global one)."""
    try:
        if source == "districts":
            return ds.get_districts(city or "tbilisi")
        return ds.get_products(city or "tbilisi", user_id=uid)
    except Exception:
        return {}


def _lookup_item(source: str, city: str, slug: str) -> dict | None:
    if not slug:
        return None
    try:
        if source == "districts":
            return ds.get_districts(city or "tbilisi").get(slug)
        item = ds.get_product(slug)
        if item is not None:
            return item
        return ds.get_products(city or "tbilisi").get(slug)
    except Exception:
        return None


def _apply_item_lookup(page: dict, uid: int) -> None:
    """If a page declares item_lookup, load the selected item and merge its
    fields into the session vars so templates can use {name} {price} …"""
    il = page.get("item_lookup")
    if not il:
        return
    v = _sess(uid)["vars"]
    slug = v.get(il.get("var", "product"))
    item = _lookup_item(il.get("source", "products"), il.get("city", "tbilisi"), slug)
    if not item:
        return
    v["name"] = item.get("name", "")
    v["description"] = item.get("description", "")
    if "price_usd" in item:
        v["price"] = item.get("price_usd")


def _list_keyboard(page: dict, lang: str, default_lang: str, ctx: dict, uid: int) -> InlineKeyboardMarkup:
    """Keyboard for a `list` page: one button per data item, then static rows."""
    source = page.get("source", "products")
    city = page.get("city", "tbilisi")
    item_var = page.get("item_var", "product")
    target = page.get("item_target", "main")
    rows: list[list[InlineKeyboardButton]] = []
    for slug, item in _list_items(source, city, uid).items():
        rows.append([InlineKeyboardButton(
            text=str(item.get("name", slug)),
            callback_data=f"pick|{item_var}|{target}|{slug}",
        )])
    # static extra rows (e.g. a back button) reuse the normal builder
    extra = _keyboard(page, lang, default_lang, ctx, uid)
    rows.extend(extra.inline_keyboard)
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


async def _run_on_enter(page: dict, uid: int, bot, target, lang: str, default_lang: str) -> None:
    """Page hooks configured in the constructor (see module docstring)."""
    hook = page.get("on_enter")
    if not isinstance(hook, dict) or not hook:
        return
    v = _sess(uid)["vars"]
    for k, val in (hook.get("set_vars") or {}).items():
        v[str(k)] = val
    ctx = await _context(uid, bot)
    user = target.from_user
    ctx["username"] = f"@{user.username}" if user.username else "no-username"
    note = _pick(hook.get("notify_admins", ""), lang, default_lang)
    if note:
        text = _fmt(note, ctx)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text)
            except Exception:
                pass
    for extra in hook.get("send") or []:
        if not isinstance(extra, dict):
            continue
        text = _fmt(_pick(extra.get("text", ""), lang, default_lang), ctx)
        img = _image_path(extra.get("image", ""))
        try:
            if img:
                kind = _media_kind(img)
                pm = page.get("parse_mode") or None
                if kind == "video":
                    await bot.send_video(uid, FSInputFile(img), caption=text or None, parse_mode=pm)
                elif kind == "animation":
                    await bot.send_animation(uid, FSInputFile(img), caption=text or None, parse_mode=pm)
                else:
                    await bot.send_photo(uid, FSInputFile(img), caption=text or None, parse_mode=pm)
            elif text:
                await bot.send_message(uid, text, parse_mode=page.get("parse_mode") or None)
        except Exception:
            pass


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

    # merge selected product/district fields into session vars for templating
    _apply_item_lookup(page, uid)

    # input page: arm the awaiting-input state, then render its prompt as a message
    if ptype == "input":
        inp = page.get("input", {}) or {}
        _sess(uid)["await"] = {"page": page_id, "cfg": inp}

    # receipt page: compute dynamic values, use QR as image
    qr_override = None
    if ptype == "receipt":
        qr_override, _ = await _compute_receipt(page, uid)

    await _run_on_enter(page, uid, bot, target, lang, default_lang)

    ctx = await _context(uid, bot)
    raw_text = _pick(page.get("text", ""), lang, default_lang)
    parse_mode = page.get("parse_mode") or None
    if ptype == "history" or "{purchases" in raw_text:
        ctx.update(_history_ctx(page, uid, lang, default_lang, parse_mode))
    caption = _fmt(raw_text, ctx)
    if ptype == "list":
        kb = _list_keyboard(page, lang, default_lang, ctx, uid)
    else:
        kb = _keyboard(page, lang, default_lang, ctx, uid)
    img = qr_override or _image_path(page.get("image", ""))

    await _emit(message, caption, parse_mode, kb, img, is_start=is_start)


def _msg_kind(message) -> str:
    if message.animation:
        return "animation"
    if message.video:
        return "video"
    if message.photo:
        return "photo"
    return "text"


async def _emit(message, caption, parse_mode, kb, img, *, is_start=False):
    kind = _media_kind(img) if img else None
    if is_start:
        if img:
            await _send_media(message, kind, img, caption, parse_mode, kb)
        else:
            await message.answer(text=caption or " ", parse_mode=parse_mode, reply_markup=kb)
        return
    try:
        cur = _msg_kind(message)
        if img and cur != "text":
            await message.edit_media(media=_input_media(kind, img, caption, parse_mode), reply_markup=kb)
            return
        if not img and cur == "text":
            await message.edit_text(text=caption or " ", parse_mode=parse_mode, reply_markup=kb)
            return
    except Exception:
        pass
    try:
        await message.delete()
    except Exception:
        pass
    if img:
        await _send_media(message, kind, img, caption, parse_mode, kb)
    else:
        await message.answer(text=caption or " ", parse_mode=parse_mode, reply_markup=kb)


# ── purchase history ──────────────────────────────────────────────────────────
_HIST_LINE = "🛒 {date} — {product} — {price}$"
_HIST_EMPTY = {"ka": "შესყიდვები ჯერ არ არის.", "ru": "Покупок пока нет.", "en": "No purchases yet."}


def _history_ctx(page: dict, uid: int, lang: str, default_lang: str, parse_mode) -> dict:
    h = page.get("history") or {}
    try:
        limit = max(1, min(int(h.get("limit", 10)), 30))
    except (TypeError, ValueError):
        limit = 10
    try:
        items = ds.get_user_purchases(uid)
    except Exception:
        items = []
    line = _pick(h.get("line", ""), lang, default_lang) or _HIST_LINE
    esc = (lambda x: html.escape(str(x))) if str(parse_mode).upper() == "HTML" else str
    out = []
    for p in items[:limit]:
        price = p.get("price_usd", 0)
        price = int(price) if float(price) == int(price) else price
        out.append(_fmt(line, {"date": esc(p.get("date", "")), "product": esc(p.get("product", "")),
                               "price": price, "note": esc(p.get("note", "")), "id": p.get("id", "")}))
    if not out:
        out = [_pick(h.get("empty", ""), lang, default_lang) or _HIST_EMPTY.get(lang) or _HIST_EMPTY["en"]]
    total = round(sum(float(p.get("price_usd", 0) or 0) for p in items), 2)
    return {"purchases": "\n".join(out), "purchase_count": len(items), "purchase_total": total}


# ── channel gate ──────────────────────────────────────────────────────────────
_GATE_TEXT = {"ka": "👋 გასაგრძელებლად გამოიწერეთ ჩვენი არხი, შემდეგ დააჭირეთ „შემოწმება“.",
              "ru": "👋 Чтобы продолжить, подпишитесь на наш канал, затем нажмите «Проверить».",
              "en": "👋 To continue, please join our channel, then press “Check”."}
_GATE_JOIN = {"ka": "📢 არხზე გადასვლა", "ru": "📢 Перейти в канал", "en": "📢 Join channel"}
_GATE_CHECK = {"ka": "✅ შემოწმება", "ru": "✅ Проверить", "en": "✅ Check"}
_GATE_FAIL = {"ka": "❌ ჯერ არ ხართ გამოწერილი.", "ru": "❌ Вы ещё не подписаны.", "en": "❌ You haven't joined yet."}


def _gate_cfg() -> dict | None:
    g = load_config().get("gate")
    return g if isinstance(g, dict) and g.get("enabled") else None


def _gate_text(g: dict, key: str, defaults: dict, lang: str, default_lang: str) -> str:
    return _pick(g.get(key, ""), lang, default_lang) or defaults.get(lang) or defaults["en"]


async def _is_member(bot, channel: str, uid: int) -> bool | None:
    """True/False, or None when Telegram can't tell (bot not in the channel, bad id…)."""
    try:
        chat = int(channel) if channel.lstrip("-").isdigit() else channel
        m = await bot.get_chat_member(chat, uid)
    except Exception:
        return None
    status = getattr(m.status, "value", m.status)
    if status in ("creator", "administrator", "member"):
        return True
    if status == "restricted":
        return bool(getattr(m, "is_member", False))
    return False


def _gate_kb(g: dict, lang: str, default_lang: str) -> InlineKeyboardMarkup:
    rows = []
    url = str(g.get("url", "") or "")
    if url:
        rows.append([InlineKeyboardButton(text=_gate_text(g, "join_label", _GATE_JOIN, lang, default_lang), url=url)])
    rows.append([InlineKeyboardButton(text=_gate_text(g, "check_label", _GATE_CHECK, lang, default_lang),
                                      callback_data="gate:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_gate(target, *, is_start: bool = False) -> None:
    g = _gate_cfg()
    if not g:
        return
    cfg = load_config()
    default_lang = cfg.get("default_lang", "ka")
    message = target.message if isinstance(target, CallbackQuery) else target
    lang = _lang(target.from_user.id, cfg)
    text = _gate_text(g, "text", _GATE_TEXT, lang, default_lang)
    img = _image_path(g.get("image", ""))
    await _emit(message, text, "HTML", _gate_kb(g, lang, default_lang), img, is_start=is_start)


def gate_blocks(uid: int) -> bool:
    """True when the gate is on and this user still has to pass it."""
    return bool(_gate_cfg()) and uid not in ADMIN_IDS and not ds.is_gate_ok(uid)


class GateMiddleware(BaseMiddleware):
    """Stops gated users from using the bot until they pass the channel check."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or not gate_blocks(user.id):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            if (event.data or "").startswith("gate:"):
                return await handler(event, data)
            await show_gate(event)
            await event.answer()
            return None
        if isinstance(event, Message):
            if (event.text or "").startswith("/start"):
                return await handler(event, data)
            await show_gate(event, is_start=True)
            return None
        return await handler(event, data)


def install_gate(dp) -> None:
    mw = GateMiddleware()
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)


# ── routes ────────────────────────────────────────────────────────────────────
@engine_router.message(CommandStart())
async def engine_start(message: Message) -> None:
    cfg = load_config()
    try:
        ds.register_user(message.from_user.id, message.from_user.username or "")
    except Exception:
        pass
    uid = message.from_user.id
    _sess(uid)["await"] = None  # reset any pending input
    g = _gate_cfg()
    if g and uid not in ADMIN_IDS:
        if g.get("mode") == "dummy":
            passed = ds.is_gate_ok(uid)
        else:
            member = await _is_member(message.bot, str(g.get("channel", "")), uid)
            passed = ds.is_gate_ok(uid) if member is None else member
            if passed != ds.is_gate_ok(uid):
                ds.set_gate_ok(uid, passed)
        if not passed:
            await show_gate(message, is_start=True)
            return
    await render_page(message, cfg.get("start_page", "main"), is_start=True)


@engine_router.callback_query(F.data == "gate:check")
async def engine_gate_check(callback: CallbackQuery) -> None:
    g = _gate_cfg()
    uid = callback.from_user.id
    if not g or uid in ADMIN_IDS or ds.is_gate_ok(uid):
        await render_page(callback, load_config().get("start_page", "main"))
        await callback.answer()
        return
    cfg = load_config()
    try:
        ds.register_user(uid, callback.from_user.username or "")
    except Exception:
        pass
    lang = _lang(uid, cfg)
    default_lang = cfg.get("default_lang", "ka")
    if g.get("mode") == "dummy":
        member: bool | None = True
    else:
        member = await _is_member(callback.bot, str(g.get("channel", "")), uid)
    if member:
        ds.set_gate_ok(uid, True)
        await render_page(callback, cfg.get("start_page", "main"))
        await callback.answer()
    else:
        await callback.answer(_gate_text(g, "fail_text", _GATE_FAIL, lang, default_lang), show_alert=True)


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


@engine_router.callback_query(F.data.startswith("pick|"))
async def engine_pick(callback: CallbackQuery) -> None:
    # pick|<item_var>|<target_page>|<slug>   (slug may contain ':')
    try:
        _, item_var, target, slug = callback.data.split("|", 3)
    except ValueError:
        await callback.answer(); return
    _sess(callback.from_user.id)["vars"][item_var] = slug
    await render_page(callback, target)
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
