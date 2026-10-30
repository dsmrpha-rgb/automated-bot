"""
Config-driven flow engine.

Turns menu_config.json into the ENTIRE user-facing bot: pages, captions
(multi-language), images, inline-button layouts, multi-step input flows and
dynamic crypto top-up receipts — all editable from the Bot Panel constructor
without touching code.

List pages (type "list") — one button per item; picking stores the slug in item_var and
opens item_target. Sources: products (category_var filters by a picked category),
districts (product_var keeps only the districts of the picked product), categories,
cities. city_var takes the city from a session variable instead of a fixed city.
In HTML/Markdown pages {wallet} is shown tap-to-copy automatically (monospace) — wrapping it in
<code> yourself is fine too. A page with image_from "district" shows the picture attached to the district the user picked
(Global Admin / bot admin menu), falling back to its own picture; on payment screens it replaces
the QR code. Picked values are also readable as {city_name} {category_name} {product_name}
{district_name} {product_price}; a "variant" variable is kept as typed.

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
                                  (value "@" = the pressed button's own label)
  confirm:<page>                  ping admins ("user paid"), then navigate
  buy:<ok>[:<low>]                (hidden while the balance is below the picked product's price,
                                  unless the button has "always": true)
                                  buy the picked product with the user's balance, deliver its
                                  delivery text/picture, then show <ok>; <low> (optional) is shown
                                  when the balance is too small ({shortage}), else a popup
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

Converted prices: {BTC_price} / {LTC_price} / {USDT_price} … in a text or button label show the
picked product's price in that coin; {BTC_amount} … does the same for the amount the user typed
(top-ups). Any coin enabled in the bot's payment settings works.

Templating: captions and labels are `.format`-ed with the user's session
variables plus {cities} {products_count} {purchase_count} {purchase_total} {balance} {bot_username} {user_id}
{referrals} {earned} {ref_link} {ref_percent}.
Referrals (config "referral": {enabled, percent}): {ref_link} is the user's invite link
(https://t.me/<bot>?start=<user id>); a new user who starts the bot through it is linked to the inviter,
who then earns {ref_percent}% of that user's purchases (balance buys and purchases recorded by admins).
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
import settings_store
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

# values shown with the bot's currency symbol once a currency is configured
_MONEY_VARS = ("balance", "price", "product_price", "amount", "purchase_total", "earned", "shortage")

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
    try:
        for k, v in ds.get_counts().items():
            ctx.setdefault(k, v)
        mine = ds.get_user_purchases(uid)
        ctx.setdefault("purchase_count", len(mine) + len(_fake_for(uid) or []))
        ctx.setdefault("purchase_total", round(sum(float(x.get("price_usd", 0) or 0) for x in mine), 2))
    except Exception:
        pass
    try:
        ctx.setdefault("referrals", ds.count_referrals(uid))
        ctx.setdefault("earned", ds.get_ref_earned(uid))
    except Exception:
        ctx.setdefault("referrals", 0)
        ctx.setdefault("earned", 0.0)
    ctx.setdefault("ref_link", f"https://t.me/{ctx.get('bot_username', '')}?start={uid}")
    pct = (settings_store.referral() or {}).get("percent", 0)
    ctx.setdefault("ref_percent", f"{pct:g}")
    cur = settings_store.currency()
    if cur:   # money values read "25ლ" / "25.85ლ"; {name_num} keeps the plain number
        for key in _MONEY_VARS:
            if key in ctx and isinstance(ctx[key], (int, float)) and not isinstance(ctx[key], bool):
                ctx[key + "_num"] = ctx[key]
                ctx[key] = settings_store.format_amount(ctx[key], cur)
    return ctx


def _label(btn: dict, lang: str, default_lang: str, ctx: dict) -> str:
    text = _fmt(_pick(btn.get("label", ""), lang, default_lang), ctx)
    style = btn.get("style")
    if style in STYLE_PREFIX:
        text = STYLE_PREFIX[style] + text
    return text


def _can_buy(uid: int) -> bool:
    """True when the picked product exists, is in stock and the user's balance covers its price."""
    try:
        slug = _sess(uid)["vars"].get("product")
        product = ds.get_product(slug) if slug else None
        if not product or not ds.product_allowed(uid, slug) or product.get("quantity", -1) == 0:
            return False
        return ds.get_user_balance(uid) + 1e-9 >= float(product.get("price_usd", 0) or 0)
    except Exception:
        return False


def _keyboard(page: dict, lang: str, default_lang: str, ctx: dict, uid: int, row_offset: int = 0) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in page.get("rows", []):
        built: list[InlineKeyboardButton] = []
        for btn in row:
            label = _label(btn, lang, default_lang, ctx)
            action = str(btn.get("action", "noop"))
            if action.startswith("url:"):
                built.append(InlineKeyboardButton(text=label, url=action[4:]))
            elif action.startswith("buy:") and not btn.get("always") and not _can_buy(uid):
                continue    # picked product costs more than the balance (or none picked / sold out)
            elif action == "admin":
                if uid in ADMIN_IDS:
                    built.append(InlineKeyboardButton(text=label, callback_data=ADMIN_BUTTON_CB))
            elif action.startswith("builtin:"):
                built.append(InlineKeyboardButton(text=label, callback_data=BUILTINS.get(action[8:], "noop")))
            elif action.startswith("choose:") and action.split(":", 3)[2:3] == ["@"]:
                # value "@" = "same as this button's label": remember where the button sits
                # (row.col in the final keyboard) so the press can read its text back
                _, var, _v, target = action.split(":", 3)
                built.append(InlineKeyboardButton(
                    text=label, callback_data=f"choose:{var}:@{len(rows) + row_offset}.{len(built)}:{target}"))
            elif action.startswith("page:") or action.startswith("choose:") or action.startswith("confirm:") or action.startswith("buy:"):
                built.append(InlineKeyboardButton(text=label, callback_data=action))
            else:
                built.append(InlineKeyboardButton(text=label, callback_data="noop"))
        if built:
            rows.append(built)
    if page.get("show_admin_button") and uid in ADMIN_IDS:
        rows.append([InlineKeyboardButton(text=ADMIN_BUTTON_TEXT, callback_data=ADMIN_BUTTON_CB)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── catalog (products / districts) ───────────────────────────────────────────
def _page_city(page: dict, uid: int | None) -> str:
    """City of a list page: taken from a session variable (city_var) or fixed."""
    var = page.get("city_var")
    val = _sess(uid)["vars"].get(var) if (var and uid is not None) else None
    return str(val or page.get("city") or "tbilisi")


def _list_items(page: dict, uid: int | None = None) -> dict:
    """Return {slug: item_dict} for a data-driven list.

    sources: products (optionally only one category), districts (optionally only the
    districts assigned to the picked product), categories, cities.
    A user's private listing replaces the global products.
    """
    source = page.get("source", "products")
    city = _page_city(page, uid)
    v = _sess(uid)["vars"] if uid is not None else {}
    try:
        if source == "cities":
            return {c: {"name": c.replace("_", " ").title()} for c in ds.get_cities()}
        if source == "districts":
            items = ds.get_districts(city)
            pv = page.get("product_var")
            if pv and v.get(pv):
                allowed = ds.get_product_districts(v[pv])
                if allowed:
                    items = {k: x for k, x in items.items() if k in allowed}
            return items
        prods = ds.get_products(city, user_id=uid)
        if source == "categories":
            cats = ds.get_categories(city)
            if page.get("hide_empty", True):
                used = {p.get("category") for p in prods.values()}
                cats = {k: x for k, x in cats.items() if k in used}
            return cats
        cv = page.get("category_var")
        if cv and v.get(cv):
            prods = {k: x for k, x in prods.items() if x.get("category") == v[cv]}
        return prods
    except Exception:
        return {}


def _lookup_item(source: str, city: str, slug: str) -> dict | None:
    if not slug:
        return None
    try:
        if source == "cities":
            return {"name": str(slug).replace("_", " ").title()}
        if source == "categories":
            return ds.get_categories().get(slug)
        if source == "districts":
            return ds.get_districts(city or "tbilisi").get(slug) or ds._load().get("districts", {}).get(slug)
        item = ds.get_product(slug)
        if item is not None:
            return item
        return ds.get_products(city or "tbilisi").get(slug)
    except Exception:
        return None


def _derive_vars(uid: int) -> None:
    """Keep readable values for what the user picked: {product_name} {product_price}
    {category_name} {district_name} {city_name} (variant is stored as-is)."""
    v = _sess(uid)["vars"]
    for var, source, prefix in (("product", "products", "product"), ("category", "categories", "category"),
                                ("district", "districts", "district"), ("city", "cities", "city")):
        slug = v.get(var)
        item = _lookup_item(source, v.get("city") or "tbilisi", slug) if slug else None
        if item:
            v[prefix + "_name"] = item.get("name", "")
            if source in ("districts", "categories"):
                v[prefix + "_image"] = item.get("image", "") or ""
            if source == "products" and "price_usd" in item:
                v["product_price"] = item.get("price_usd")


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
    item_var = page.get("item_var", "product")
    target = page.get("item_target", "main")
    rows: list[list[InlineKeyboardButton]] = []
    for slug, item in _list_items(page, uid).items():
        rows.append([InlineKeyboardButton(
            text=str(item.get("name", slug)),
            callback_data=f"pick|{item_var}|{target}|{slug}",
        )])
    # static extra rows (e.g. a back button) reuse the normal builder
    extra = _keyboard(page, lang, default_lang, ctx, uid, row_offset=len(rows))
    rows.extend(extra.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── receipt computation ──────────────────────────────────────────────────────
def _coin_code(value) -> str | None:
    """Coin code for a remembered value: 'BTC', or a button text like 'Bitcoin' / 'USDT TRC20'."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().upper()
    coins = settings_store.coins()
    for c in coins:
        if text == c["code"]:
            return c["code"]
    for c in coins:
        if text == c["label"].strip().upper():
            return c["code"]
    first = re.split(r"[\s:(\-]+", text)[0]
    for c in coins:
        if first == c["code"] or first == c["label"].strip().upper().split(" ")[0]:
            return c["code"]
    return None


def _resolve_coin(v: dict, rc: dict) -> str:
    """Which coin a payment screen is for: fixed, the configured variable, or - when no button
    stored it there - the most recently remembered value that names an enabled coin."""
    if rc.get("coin_fixed"):
        return str(rc["coin_fixed"]).upper()
    code = _coin_code(v.get(rc.get("coin_var", "crypto")))
    if code:
        return code
    for key, val in reversed(list(v.items())):
        if key == "coin":
            continue
        code = _coin_code(val)
        if code:
            return code
    coins = settings_store.coins()
    return coins[0]["code"] if coins else "BTC"


async def _compute_receipt(page: dict, uid: int) -> tuple[str | None, dict]:
    """Fill session vars for a receipt page. Returns (qr_path_or_None, ctx)."""
    from config import CRYPTO_WALLETS
    from crypto_rates import get_usd_to_crypto
    from qr_generator import generate_qr

    rc = page.get("receipt", {}) or {}
    v = _sess(uid)["vars"]

    coin = _resolve_coin(v, rc)
    amount = float(v.get(rc.get("amount_var", "amount"), 0) or 0)
    fiat_to_usd = float(rc.get("fiat_to_usd", 1.0))
    # amounts are in the bot's currency; refresh its live USD rate (no-op without a currency)
    try:
        from crypto_rates import refresh_fiat_rate
        await refresh_fiat_rate()
    except Exception:
        pass
    usd_amount = round(amount * fiat_to_usd * settings_store.usd_per_unit(), 2)

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
    _derive_vars(uid)
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
    raw_text = _wrap_wallet(raw_text, parse_mode)
    await _crypto_ctx(page, raw_text, lang, default_lang, uid, ctx)
    caption = _fmt(raw_text, ctx)
    if ptype == "list":
        kb = _list_keyboard(page, lang, default_lang, ctx, uid)
    else:
        kb = _keyboard(page, lang, default_lang, ctx, uid)
    # "image_from": show the picture of the picked district/category when it has one
    picked_img = None
    src = page.get("image_from")
    if src in ("district", "category"):
        picked_img = _image_path(_sess(uid)["vars"].get(src + "_image", ""))
    img = picked_img or qr_override or _image_path(page.get("image", ""))

    await _emit(message, caption, parse_mode, kb, img, is_start=is_start)


def _msg_kind(message) -> str:
    if message.animation:
        return "animation"
    if message.video:
        return "video"
    if message.photo:
        return "photo"
    return "text"


# which media file each chat message currently shows: {(chat_id, message_id): path}.
# When the next screen uses the same file only the caption/buttons are edited, so a
# video or GIF keeps playing instead of restarting.
_SHOWN: dict[tuple, str] = {}


def _mkey(message):
    chat = getattr(message, "chat", None)
    return (getattr(chat, "id", None), getattr(message, "message_id", None))


def _remember(sent, img):
    if len(_SHOWN) > 5000:
        _SHOWN.clear()
    if sent is not None and img:
        _SHOWN[_mkey(sent)] = img


async def _emit(message, caption, parse_mode, kb, img, *, is_start=False):
    kind = _media_kind(img) if img else None
    if is_start:
        if img:
            _remember(await _send_media(message, kind, img, caption, parse_mode, kb), img)
        else:
            await message.answer(text=caption or " ", parse_mode=parse_mode, reply_markup=kb)
        return
    try:
        cur = _msg_kind(message)
        if img and cur != "text" and _SHOWN.get(_mkey(message)) == img:
            try:
                await message.edit_caption(caption=caption, parse_mode=parse_mode, reply_markup=kb)
            except Exception as e:
                if "not modified" not in str(e).lower():
                    raise
            return
        if img and cur != "text":
            await message.edit_media(media=_input_media(kind, img, caption, parse_mode), reply_markup=kb)
            _remember(message, img)
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
        _remember(await _send_media(message, kind, img, caption, parse_mode, kb), img)
    else:
        await message.answer(text=caption or " ", parse_mode=parse_mode, reply_markup=kb)


# ── copiable wallet address ───────────────────────────────────────────────────
_WALLET_HTML = re.compile(r"(<(?:code|pre)>\s*)?\{wallet\}(\s*</(?:code|pre)>)?", re.I)
_WALLET_MD = re.compile(r"(`\s*)?\{wallet\}(\s*`)?")


def _wrap_wallet(text: str, parse_mode) -> str:
    """Make {wallet} tap-to-copy: monospace in HTML/Markdown, unless the text already does it."""
    if "{wallet}" not in text:
        return text
    mode = str(parse_mode or "").lower()
    if mode == "html":
        return _WALLET_HTML.sub(lambda m: m.group(0) if (m.group(1) and m.group(2)) else "<code>{wallet}</code>", text)
    if mode == "markdown":
        return _WALLET_MD.sub(lambda m: m.group(0) if (m.group(1) and m.group(2)) else "`{wallet}`", text)
    return text


# ── converted prices ({BTC_price} {USDT_amount} …) ─────────────────────────────
_CRYPTO_VAR = re.compile(r"\{([A-Z0-9]{2,10})_(price|amount)\}")


def _crypto_text(coin: str, value: float) -> str:
    from crypto_rates import _is_stable
    if _is_stable(coin):
        return f"{value:.2f}"
    out = f"{value:.8f}".rstrip("0")
    return out + "0" if out.endswith(".") else out


async def _crypto_ctx(page: dict, raw_text: str, lang: str, default_lang: str, uid: int, ctx: dict) -> None:
    """Fill {COIN_price} (the picked product's price) and {COIN_amount} (the number the user typed)
    with the crypto equivalent, for every such placeholder used by this page's text or buttons."""
    strings = [raw_text]
    for row in page.get("rows", []):
        for btn in row:
            strings.append(_pick(btn.get("label", ""), lang, default_lang))
    found = {m for s in strings for m in _CRYPTO_VAR.findall(s)}
    if not found:
        return
    from crypto_rates import get_usd_to_crypto, refresh_fiat_rate
    try:
        await refresh_fiat_rate()
    except Exception:
        pass
    allowed = {c["code"] for c in settings_store.coins()}
    v = _sess(uid)["vars"]
    rate = settings_store.usd_per_unit()
    for coin, kind in found:
        key = f"{coin}_{kind}"
        base = (v.get("product_price") if v.get("product_price") is not None else v.get("price")) if kind == "price" else v.get("amount")
        try:
            usd = float(base) * rate
            ctx[key] = _crypto_text(coin, await get_usd_to_crypto(coin, usd)) if coin in allowed and usd > 0 else "—"
        except (TypeError, ValueError):
            ctx[key] = "—"


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
    cur = settings_store.currency()
    line = _pick(h.get("line", ""), lang, default_lang) or (_HIST_LINE.replace("{price}$", "{price}") if cur else _HIST_LINE)
    esc = (lambda x: html.escape(str(x))) if str(parse_mode).upper() == "HTML" else str
    out = []
    for p in items[:limit]:
        price = p.get("price_usd", 0)
        price = settings_store.format_amount(price, cur) if cur else (int(price) if float(price) == int(price) else price)
        out.append(_fmt(line, {"date": esc(p.get("date", "")), "product": esc(p.get("product", "")),
                               "price": price, "note": esc(p.get("note", "")), "id": p.get("id", "")}))
    fake_cfg = _fake_cfg()
    fake = _fake_for(uid) or []
    if fake and fake_cfg:    # demo purchases: links to the products, after the real ones
        fline = _pick(fake_cfg.get("line", ""), lang, default_lang) or _FAKE_LINE
        for n, url in enumerate(fake, 1):
            out.append(_fmt(fline, {"n": n, "link": esc(url), "url": esc(url), "product": esc(url),
                                    "date": "", "price": "", "note": "", "id": ""}))
    if not out:
        out = [_pick(h.get("empty", ""), lang, default_lang) or _HIST_EMPTY.get(lang) or _HIST_EMPTY["en"]]
    total = round(sum(float(p.get("price_usd", 0) or 0) for p in items), 2)
    return {"purchases": "\n".join(out), "purchase_count": len(items) + len(fake), "purchase_total": total}


# ── demo balance ──────────────────────────────────────────────────────────────
def _give_demo_balance(uid: int) -> None:
    """Switch on in the constructor (Settings → Demo balance): a user who starts the bot gets a
    random balance with decimals, e.g. 7.35 — whatever the currency. Decided once per user."""
    demo = settings_store.demo_balance()
    if not demo:
        return
    try:
        amount = round(random.uniform(demo["min"], demo["max"]), 2)
        for _ in range(8):      # prefer a real decimal (7.35) over a round number (7.0)
            if amount != int(amount) or demo["max"] - demo["min"] < 1:
                break
            amount = round(random.uniform(demo["min"], demo["max"]), 2)
        ds.give_demo_balance(uid, amount)
    except Exception:
        pass


# ── demo purchases ────────────────────────────────────────────────────────────
# Optional dummy feature (history page → history.fake): every user gets a random number of
# "purchases" (1-3) shown as links on the purchase-history screen. Real purchases still register.
_FAKE_LINE = "🛒 {n}. {link}"


def _fake_cfg() -> dict | None:
    for page in load_config().get("pages", {}).values():
        fake = (page.get("history") or {}).get("fake") if page.get("type") == "history" else None
        if isinstance(fake, dict) and fake.get("enabled") and fake.get("links"):
            return fake
    return None


def _fake_for(uid: int) -> list[str] | None:
    """The user's demo links (assigning a random 1-3 of them the first time); None when the switch is off."""
    fake = _fake_cfg()
    if not fake:
        return None
    links = [str(x) for x in fake.get("links", []) if str(x).startswith("http")]
    if not links:
        return None
    try:
        mine = ds.get_demo_purchases(uid)
        if mine is None:
            lo = max(1, int(fake.get("min", 1)))
            hi = max(lo, min(int(fake.get("max", 3)), len(links)))
            mine = random.sample(links, min(random.randint(lo, hi), len(links)))
            ds.set_demo_purchases(uid, mine)
        return [u for u in mine if u in links] or mine
    except Exception:
        return None


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
    is_new = False
    try:
        is_new = ds.register_user(message.from_user.id, message.from_user.username or "")
    except Exception:
        pass
    uid = message.from_user.id
    if is_new and settings_store.referral():     # /start <inviter id>: remember who invited this user
        payload = (message.text or "").split(maxsplit=1)
        if len(payload) == 2 and payload[1].strip().isdigit():
            try:
                ds.set_referrer(uid, int(payload[1].strip()))
            except Exception:
                pass
    _sess(uid)["await"] = None  # reset any pending input
    _give_demo_balance(uid)     # demo balance: a random amount, once (no-op when the switch is off)
    _fake_for(uid)              # demo purchases: assign this user's random 1-3 (no-op when the switch is off)
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


def _pressed_label(callback: CallbackQuery, pos: str) -> str:
    """Text of the button that was pressed (value "@r.c" = row.col), without the colour emoji."""
    try:
        r, c = (int(x) for x in pos.split("."))
        text = callback.message.reply_markup.inline_keyboard[r][c].text
    except Exception:
        return ""
    for prefix in STYLE_PREFIX.values():
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.strip()


@engine_router.callback_query(F.data.startswith("choose:"))
async def engine_choose(callback: CallbackQuery) -> None:
    # choose:<var>:<value>:<page>
    try:
        _, var, value, page = callback.data.split(":", 3)
    except ValueError:
        await callback.answer(); return
    if value.startswith("@"):
        value = _pressed_label(callback, value[1:])
    v = _sess(callback.from_user.id)["vars"]
    v.pop(var, None)          # re-insert so the newest choice is last (see _resolve_coin)
    v[var] = value
    await render_page(callback, page)
    await callback.answer()


async def _pay_referral_bonus(bot, uid: int, price: float) -> None:
    """Referral programme: the user's inviter earns a percentage of every purchase."""
    prog = settings_store.referral()
    if not prog:
        return
    try:
        res = ds.apply_referral_bonus(uid, price, prog["percent"])
        if res:
            import texts
            ref_id, bonus = res
            await bot.send_message(ref_id, texts.t("REF_BONUS_NOTE", _lang(ref_id, load_config()))
                                   .format(amount=settings_store.format_amount(bonus)))
    except Exception:
        pass


async def _send_delivery(bot, uid: int, product: dict) -> None:
    """Deliver what the buyer paid for: the product's delivery text and picture/GIF/video."""
    text = (product.get("delivery_text") or "").strip()
    img = _image_path((product.get("delivery_image") or "").strip())
    try:
        if img:
            kind = _media_kind(img)
            cap = text if len(text) <= 1024 else None
            if kind == "video":
                await bot.send_video(uid, FSInputFile(img), caption=cap)
            elif kind == "animation":
                await bot.send_animation(uid, FSInputFile(img), caption=cap)
            else:
                await bot.send_photo(uid, FSInputFile(img), caption=cap)
            if text and cap is None:
                await bot.send_message(uid, text)
        elif text:
            await bot.send_message(uid, text)
    except Exception:
        import logging
        logging.exception("Failed to deliver product %s to %s", product.get("name"), uid)


@engine_router.callback_query(F.data.startswith("buy:"))
async def engine_buy(callback: CallbackQuery) -> None:
    """buy:<ok_page>[:<low_balance_page>] — buy the picked product with the user's balance,
    then deliver its text/picture (same flow as the Nestle bot)."""
    import texts
    parts = callback.data.split(":")
    ok_page = parts[1] if len(parts) > 1 else ""
    low_page = parts[2] if len(parts) > 2 else ""
    uid = callback.from_user.id
    cfg = load_config()
    lang = _lang(uid, cfg)
    v = _sess(uid)["vars"]
    slug = v.get("product")
    product = ds.get_product(slug) if slug else None
    if not product or not ds.product_allowed(uid, slug):
        await callback.answer(texts.t("BUY_NO_PRODUCT", lang), show_alert=True)
        return
    price = float(product.get("price_usd", 0) or 0)
    balance = ds.get_user_balance(uid)
    if balance + 1e-9 < price:
        v["shortage"] = round(price - balance, 2)
        shortage = settings_store.format_amount(v["shortage"]) if settings_store.currency() else v["shortage"]
        if low_page:
            await render_page(callback, low_page)
            await callback.answer()
        else:
            await callback.answer(texts.t("BUY_NOT_ENOUGH", lang).format(shortage=shortage), show_alert=True)
        return
    if product.get("quantity", -1) == 0:
        await callback.answer(texts.t("BUY_OUT_OF_STOCK", lang), show_alert=True)
        return
    if not ds.deduct_balance(uid, price):
        await callback.answer(texts.t("BUY_NOT_ENOUGH", lang).format(shortage=price), show_alert=True)
        return
    ds.take_stock(slug)
    note = " · ".join(f"{k}: {v[x]}" for k, x in (("city", "city_name"), ("category", "category_name"),
                      ("district", "district_name"), ("variant", "variant")) if v.get(x))
    try:
        ds.add_purchase(uid, product.get("name", slug), price, note, by="balance")
        ds.increment_order(price)
    except Exception:
        pass
    un = f"@{callback.from_user.username}" if callback.from_user.username else "no-username"
    admin_note = (f"🛒 {un} (id {uid}) bought “{product.get('name', slug)}” for "
                  f"{settings_store.format_amount(price)} from balance." + (f"\n{note}" if note else ""))
    for admin_id in ADMIN_IDS:
        try:
            await callback.bot.send_message(admin_id, admin_note)
        except Exception:
            pass
    v["price"] = price
    await _pay_referral_bonus(callback.bot, uid, price)
    if ok_page:
        await render_page(callback, ok_page)
    await callback.answer()
    await _send_delivery(callback.bot, uid, product)


@engine_router.callback_query(F.data.startswith("confirm:"))
async def engine_confirm(callback: CallbackQuery) -> None:
    # confirm:<page> — ping admins that the user says they've paid, then navigate
    page = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    v = _sess(uid)["vars"]
    un = f"@{callback.from_user.username}" if callback.from_user.username else "no-username"
    amt = v.get("amount", "?")
    if isinstance(amt, (int, float)) and settings_store.currency():
        amt = settings_store.format_amount(amt)
    text = (f"💸 User {un} (id {uid}) pressed 'I paid'.\n"
            f"Request #{v.get('request_id','?')} · {v.get('crypto_amount','?')} "
            f"{v.get('coin','?')} · amount {amt}")
    picked = [f"{label}: {v[key]}" for label, key in (("City", "city_name"), ("Category", "category_name"),
              ("Product", "product_name"), ("District", "district_name"), ("Variant", "variant")) if v.get(key)]
    if picked:
        text += "\n" + " · ".join(picked)
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
