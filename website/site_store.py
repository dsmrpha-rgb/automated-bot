"""Data layer of the website: reads the shop (products, districts, categories, currency) straight from the
Telegram bot's data.json and keeps website accounts, reviews and settings in the same file, so Global Admin
and the bot see everything. Writes go through one lock shared with Global Admin."""
import fcntl
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

BOT_DIR = Path(os.environ.get("SITE_BOT_DIR", "/opt/test2"))
BOT_NAME = os.environ.get("SITE_BOT_NAME", "test2")
LOCK_PATH = Path(os.environ.get("GLOBAL_ADMIN_DIR", "/opt/bot-panel/global_admin_data")) / f"{BOT_NAME}.json.lock"
sys.path.insert(0, str(BOT_DIR))

import data_store as ds          # noqa: E402  (the bot's own store)
import settings_store            # noqa: E402

VARIANTS = {"land": "მიწა უბანი / BURRIED IN DISTRICT", "building": "კორპუსი / BUILDING"}
MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm", ".m4v"}
LOGIN_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

DEFAULT_SITE = {
    "enabled": True,
    "brand": "МАГАЗИН: CAPONE.CC",
    "name": "CAPONE.CC",
    "copyright": "© 2026 · Все права защищены",
    "coin": "LTC",
    "default_image": "",
    "city_images": {},
    "links": [
        {"title": "Contact კონტაქტი ☎ WORK / ვაკანსია", "icon": "↗",
         "items": ["https://t.me/GODALCAP", "https://t.me/askalcapone", "askcapone.cc"]},
        {"title": "Telegram", "icon": "✦", "items": ["https://t.me/caponeinfo_bot", "https://t.me/capohelp"]},
        {"title": "ბოტები - BOT", "icon": "i", "items": ["t.me/askbotebi", "https://t.me/caponesbotebi"]},
    ],
}


@contextmanager
def locked():
    """Exclusive lock around a read-modify-write of data.json (shared with Global Admin)."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        yield


def _load() -> dict:
    return ds._load()


def _save(data: dict) -> None:
    tmp = ds.DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ds.DATA_PATH)


# ── settings ────────────────────────────────────────────────────────────────
def site_settings() -> dict:
    s = json.loads(json.dumps(DEFAULT_SITE))
    saved = _load().get("site")
    if isinstance(saved, dict):
        s.update({k: v for k, v in saved.items() if k in DEFAULT_SITE})
    return s


# ── shop ────────────────────────────────────────────────────────────────────
def cities() -> list[str]:
    return list(ds.get_cities())


def settings_cities() -> list[dict]:
    imgs = site_settings().get("city_images") or {}
    return [{"name": c, "image": imgs.get(c, "")} for c in cities()]


def _public(products: dict) -> dict:
    return {k: v for k, v in products.items() if not v.get("owner")}


def city_products(city: str) -> dict:
    return _public(ds.get_products(city, all_products=True))


def city_categories(city: str) -> dict:
    """Categories of this city that currently hold at least one product."""
    prods = city_products(city)
    return {k: c for k, c in ds.get_categories(city).items() if any(p.get("category") == k for p in prods.values())}


def category_products(city: str, cat: str) -> dict:
    return {k: p for k, p in city_products(city).items() if p.get("category") == cat}


def product_districts(slug: str) -> dict:
    p = ds.get_product(slug) or {}
    all_d = _load().get("districts", {})
    return {d: all_d[d] for d in p.get("districts", []) if d in all_d}


def media_file(name: str) -> Path | None:
    if not name or os.path.basename(name) != name or Path(name).suffix.lower() not in MEDIA_EXTS:
        return None
    p = BOT_DIR / name
    return p if p.is_file() else None


# ── money ───────────────────────────────────────────────────────────────────
def currency() -> dict:
    return settings_store.currency() or {"code": "USD", "symbol": "$", "position": "after", "space": False}


def money(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    cur = currency()
    num = f"{v:.1f}" if abs(v - round(v)) < 0.005 else f"{v:.2f}".rstrip("0")
    return f"{cur['symbol']} {num}" if cur.get("position") == "before" else f"{num} {cur['symbol']}"


_rate_cache: dict = {}


def crypto_amount(price, coin: str) -> str | None:
    """Price in the bot's currency -> amount of `coin` at the live rate (cached for a minute)."""
    import asyncio
    import crypto_rates
    try:
        async def go():
            try:
                await crypto_rates.refresh_fiat_rate()
            except Exception:
                pass
            return await crypto_rates.get_crypto_price_usd(coin)
        hit = _rate_cache.get(coin)
        if not hit or time.time() - hit[1] > 60:
            _rate_cache[coin] = hit = (asyncio.run(go()), time.time())
        usd = float(price) * settings_store.usd_per_unit()
        if hit[0] <= 0 or usd <= 0:
            return None
        n = usd / hit[0]
        return (f"{n:.2f}" if crypto_rates._is_stable(coin) else f"{n:.6f}") + f" {coin}"
    except Exception:
        return None


def wallets() -> dict:
    env = {}
    try:
        for line in (BOT_DIR / ".env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return {c["code"]: env.get(c["code"] + "_WALLET", "") for c in settings_store.coins()
            if c.get("enabled", True) and env.get(c["code"] + "_WALLET")}


# ── accounts ────────────────────────────────────────────────────────────────
_attempts: dict = {}


def too_many_attempts(key: str) -> bool:
    now = time.time()
    hits = [t for t in _attempts.get(key, []) if now - t < 600]
    _attempts[key] = hits
    return len(hits) >= 8


def note_attempt(key: str) -> None:
    _attempts.setdefault(key, []).append(time.time())


def register(login: str, pw: str, pw2: str) -> tuple[bool, str]:
    login = (login or "").strip()
    if not LOGIN_RE.match(login):
        return False, "Login: 3-32 letters, digits, _ . - · ლოგინი: 3-32 სიმბოლო"
    if len(pw or "") < 6:
        return False, "Password must be at least 6 characters · პაროლი მინიმუმ 6 სიმბოლო"
    if pw != pw2:
        return False, "Passwords do not match · პაროლები არ ემთხვევა"
    with locked():
        data = _load()
        users = data.setdefault("site_users", {})
        if login.lower() in users:
            return False, "This login is taken · ეს ლოგინი დაკავებულია"
        users[login.lower()] = {"login": login, "pw": generate_password_hash(pw), "balance": 0.0, "banned": False,
                                "created": datetime.now().strftime("%Y-%m-%d %H:%M")}
        _save(data)
    return True, login


def authenticate(login: str, pw: str) -> tuple[dict | None, str]:
    u = _load().get("site_users", {}).get((login or "").strip().lower())
    if not u or not check_password_hash(u.get("pw", ""), pw or ""):
        return None, "Wrong login or password · არასწორი ლოგინი ან პაროლი"
    if u.get("banned"):
        return None, "This account is blocked · ანგარიში დაბლოკილია"
    return u, ""


def get_user(login: str) -> dict | None:
    u = _load().get("site_users", {}).get((login or "").lower())
    return None if not u or u.get("banned") else u


# ── buying ──────────────────────────────────────────────────────────────────
def buy(login: str, slug: str, variant: str, district: str) -> tuple[bool, str, dict | None]:
    with locked():
        data = _load()
        u = data.get("site_users", {}).get(login.lower())
        p = data.get("products", {}).get(slug)
        d = data.get("districts", {}).get(district)
        if not u or u.get("banned"):
            return False, "Please sign in · გთხოვთ შეხვიდეთ", None
        if not p or not d or p.get("owner") or district not in p.get("districts", []):
            return False, "This offer is no longer available · შეთავაზება აღარ არის", None
        if variant not in ds.product_variants(p):
            return False, "Wrong variant · არასწორი ვარიანტი", None
        if p.get("quantity", -1) == 0:
            return False, "Out of stock · არ არის მარაგში", None
        price = float(p.get("price_usd", 0) or 0)
        if float(u.get("balance", 0)) + 1e-9 < price:
            return False, "Not enough balance · არასაკმარისი ბალანსი", None
        u["balance"] = round(float(u["balance"]) - price, 2)
        if p.get("quantity", -1) > 0:
            p["quantity"] -= 1
        items = data.setdefault("purchases", [])
        pid = max([x.get("id", 0) for x in items] + [0]) + 1
        note = f"city: {p.get('city', '')} · district: {d.get('name', '')} · variant: {VARIANTS.get(variant, variant)}"
        rec = {"id": pid, "user_id": 0, "web_user": u["login"], "product": str(p.get("name", ""))[:200],
               "price_usd": price, "note": note, "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "by": "website",
               "slug": slug, "district": d.get("name", ""), "city": p.get("city", ""), "variant": variant}
        items.append(rec)
        st = data.setdefault("stats", {"total_orders": 0, "total_revenue_usd": 0.0})
        st["total_orders"] = st.get("total_orders", 0) + 1
        st["total_revenue_usd"] = round(st.get("total_revenue_usd", 0.0) + price, 2)
        _save(data)
    return True, "", rec


def user_orders(login: str) -> list[dict]:
    return [p for p in reversed(_load().get("purchases", [])) if (p.get("web_user") or "").lower() == login.lower()]


def get_order(pid: int, login: str) -> dict | None:
    for p in _load().get("purchases", []):
        if p.get("id") == pid and (p.get("web_user") or "").lower() == login.lower():
            return p
    return None


def delivery_for(order: dict) -> dict:
    p = ds.get_product(order.get("slug", "")) or {}
    return {"text": p.get("delivery_text", ""), "image": p.get("delivery_image", "")}


# ── reviews ─────────────────────────────────────────────────────────────────
def reviews(page: int, per: int = 6) -> tuple[list[dict], int, int]:
    ok = [r for r in _load().get("site_reviews", []) if r.get("approved")]
    ok.sort(key=lambda r: r.get("ts", 0), reverse=True)
    pages = max(1, -(-len(ok) // per))
    page = min(max(page, 1), pages)
    return ok[(page - 1) * per: page * per], pages, len(ok)


def add_review(login: str, order_id: int, text: str) -> tuple[bool, str]:
    text = (text or "").strip()[:600]
    if not text:
        return False, "Write something · დაწერეთ ტექსტი"
    with locked():
        data = _load()
        order = next((p for p in data.get("purchases", []) if p.get("id") == order_id
                      and (p.get("web_user") or "").lower() == login.lower()), None)
        if not order:
            return False, "Order not found"
        revs = data.setdefault("site_reviews", [])
        if any(r.get("order_id") == order_id for r in revs):
            return False, "You already reviewed this order · შეფასება უკვე დაწერეთ"
        revs.append({"id": max([r.get("id", 0) for r in revs] + [0]) + 1, "login": order["web_user"], "city": order.get("city", ""),
                     "district": order.get("district", ""), "product": order.get("product", ""), "text": text,
                     "order_id": order_id, "purchase": True, "approved": False,
                     "date": datetime.now().strftime("%d.%m.%Y · %H:%M"), "ts": time.time()})
        _save(data)
    return True, "Thanks! The review appears after moderation · მადლობა! შეფასება გამოჩნდება მოდერაციის შემდეგ"


# ── admin notifications (Telegram) ──────────────────────────────────────────
def notify_admins(event: str, text: str) -> None:
    """Message the bot's admins who have this event switched on (Global Admin -> Admin notifications)."""
    env = {}
    try:
        for line in (BOT_DIR / ".env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return
    tokens = [t.strip() for t in (env.get("BOT_TOKENS") or env.get("BOT_TOKEN") or "").split(",") if ":" in t]
    admins = [a.strip() for a in env.get("ADMIN_IDS", "").split(",") if a.strip().isdigit()]
    off = settings_store.cfg().get("notifications") or {}
    admins = [a for a in admins if (off.get(a) or {}).get(event) is not False]
    if not tokens or not admins:
        return

    def run():
        for a in admins:
            try:
                body = urllib.parse.urlencode({"chat_id": a, "text": text}).encode()
                urllib.request.urlopen(urllib.request.Request(
                    f"https://api.telegram.org/bot{tokens[0]}/sendMessage", data=body), timeout=10).read()
            except Exception:
                pass
    threading.Thread(target=run, daemon=True).start()
