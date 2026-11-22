"""
Global Admin — one web console for every bot registered in /etc/bot-panel/bots.json.

Bots are discovered from the panel's registry at request time and wrapped by an
adapter chosen from the bot's *data layout*, not its name, so bots created later
by the constructor (or attached by hand) show up here automatically:

  json    data.json with a "users" map          (Mama Hades / constructor engine bots)
  shop    sqlite with users(tg_user_id, ...)    (Nestle, AcidROOM)
  simple  sqlite with users(user_id, ...)       (forward / link bots)

Features: user list (IDs, usernames, balances), ban/unban, balance edits,
product management, instant + scheduled announcements, and a scan that finds
users who blocked the bot or deleted their account so they can be cleaned out.

Register in app.py with:
    from global_admin import global_bp
    app.register_blueprint(global_bp)
"""
from __future__ import annotations

import fcntl
import json
import mimetypes
import os
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import re
import uuid

import currency_util
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

global_bp = Blueprint("global_admin", __name__)

PANEL_CONFIG = Path(os.environ.get("PANEL_CONFIG", "/etc/bot-panel/bots.json"))
STATE_DIR = Path(os.environ.get("GLOBAL_ADMIN_DIR", "/opt/bot-panel/global_admin_data"))
BACKUP_DIR = Path("/var/backups/bot-panel")
PANEL_DB = STATE_DIR / "global_admin.db"
UPLOAD_DIR = STATE_DIR / "uploads"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
MAX_PHOTO_BYTES = 10 * 1024 * 1024
SKIP_BOTS = {"panel"}

STATE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_str() -> str:
    return utcnow().strftime(TIME_FMT)


# ── auth / CSRF ────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("logged_in"):
            if request.path.startswith("/global/api/"):
                return jsonify(error="not logged in"), 401
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


@global_bp.before_request
def _csrf_guard():
    # Cross-site pages cannot add custom headers without a CORS preflight.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-GA") != "1":
        return jsonify(error="missing X-GA header"), 403


# ── panel's own state db ───────────────────────────────────────────────────
def pdb() -> sqlite3.Connection:
    con = sqlite3.connect(PANEL_DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_panel_db() -> None:
    with closing(pdb()) as con, con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS user_status (
            bot TEXT NOT NULL, uid INTEGER NOT NULL,
            status TEXT NOT NULL,            -- alive | dead
            reason TEXT, username TEXT, first_name TEXT, checked_at TEXT,
            PRIMARY KEY (bot, uid));
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bot TEXT NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL, total INTEGER DEFAULT 0, done INTEGER DEFAULT 0,
            ok INTEGER DEFAULT 0, dead INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
            note TEXT, started TEXT, finished TEXT);
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bot TEXT NOT NULL, text TEXT NOT NULL,
            photo TEXT, html INTEGER NOT NULL DEFAULT 0, run_at TEXT NOT NULL,
            every_min INTEGER, created TEXT);
        """)


init_panel_db()


# ── bot registry + adapters ────────────────────────────────────────────────
def load_registry() -> dict:
    if PANEL_CONFIG.exists():
        return json.loads(PANEL_CONFIG.read_text()).get("bots", {})
    return {}


def parse_env(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def bot_tokens(bot_dir: Path) -> list[str]:
    env = parse_env(bot_dir / ".env")
    raw = env.get("BOT_TOKENS") or env.get("BOT_TOKEN") or ""
    return [t.strip() for t in raw.split(",") if ":" in t]


def _sqlite_candidates(bot_dir: Path) -> list[Path]:
    env = parse_env(bot_dir / ".env")
    cands = []
    for key in ("DB_PATH", "USERS_DB"):
        if env.get(key):
            cands.append(Path(env[key]))
    cands += [bot_dir / n for n in ("bot.sqlite3", "bot.db", "users.sqlite3")]
    return [c for c in cands if c.exists()]


def _tables(path: Path) -> dict[str, list[str]]:
    with closing(sqlite3.connect(path)) as con:
        out = {}
        for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            out[n] = [r[1] for r in con.execute(f'PRAGMA table_info("{n}")')]
        return out


class AdapterError(Exception):
    pass


class JsonAdapter:
    kind = "json"
    has_products = True
    has_balance = True
    has_ban = True

    def __init__(self, name, bot_dir: Path):
        self.name, self.dir = name, bot_dir
        self.path = bot_dir / "data.json"
        self.lock_path = STATE_DIR / f"{name}.json.lock"

    # file access: lock + atomic replace so the panel never leaves a half-written file
    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _mutate(self, fn):
        with open(self.lock_path, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            data = self._load()
            res = fn(data)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            return res

    def backup(self) -> str:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dest = BACKUP_DIR / f"global-clean-{self.name}-{utcnow().strftime('%Y%m%d-%H%M%S')}.json"
        shutil.copy2(self.path, dest)
        return str(dest)

    def users(self) -> list[dict]:
        return [{
            "id": int(uid), "username": u.get("username") or "",
            "balance": float(u.get("balance") or 0), "joined": u.get("joined") or "",
            "banned": bool(u.get("banned")),
        } for uid, u in self._load().get("users", {}).items()]

    def set_ban(self, uid, banned):
        def fn(d):
            u = d["users"].get(str(uid))
            if not u:
                raise AdapterError("user not found")
            u["banned"] = bool(banned)
        self._mutate(fn)

    def set_balance(self, uid, mode, amount):
        def fn(d):
            u = d["users"].get(str(uid))
            if not u:
                raise AdapterError("user not found")
            cur = float(u.get("balance") or 0)
            new = amount if mode == "set" else cur + amount
            u["balance"] = round(max(new, 0.0), 2)
            return u["balance"]
        return self._mutate(fn)

    def delete_users(self, ids):
        def fn(d):
            n = 0
            for i in ids:
                if d["users"].pop(str(i), None) is not None:
                    n += 1
            return n
        return self._mutate(fn)

    # products
    def meta(self):
        d = self._load()
        return {
            "fields": ["name", "price_usd", "description", "quantity", "city", "districts", "category", "delivery_text", "delivery_image", "variants", "weight", "image"],
            "districts": [{"id": k, "name": v.get("name", k), "city": v.get("city", ""), "image": v.get("image", "")}
                          for k, v in d.get("districts", {}).items()],
            "categories": [{"id": k, "name": v.get("name", k), "city": v.get("city", "")}
                           for k, v in d.get("categories", {}).items()],
            "supports_categories": self.supports_categories,
            "cities": sorted({p.get("city", "") for p in d.get("products", {}).values()}
                             | {x.get("city", "") for x in d.get("districts", {}).values()} | {"tbilisi"}),
        }

    def products(self):
        return [{"id": k, **v} for k, v in self._load().get("products", {}).items() if v.get("owner") is None]

    # private listings (bot must run the matching code, see supports_private)
    @property
    def supports_private(self) -> bool:
        try:
            return "def has_private_listing" in (self.dir / "data_store.py").read_text()
        except OSError:
            return False

    def list_private(self):
        d = self._load()
        return [{"user_id": int(uid), "products": sum(1 for p in d.get("products", {}).values() if p.get("owner") == int(uid))}
                for uid in d.get("private_listings", {})]

    def create_private(self, uid):
        self._mutate(lambda d: d.setdefault("private_listings", {}).setdefault(str(uid), {"created": now_str()[:16]}))

    def delete_private(self, uid):
        def fn(d):
            d.get("private_listings", {}).pop(str(uid), None)
            d["products"] = {k: v for k, v in d.get("products", {}).items() if v.get("owner") != uid}
        self._mutate(fn)

    def private_products(self, uid):
        return [{"id": k, **v} for k, v in self._load().get("products", {}).items() if v.get("owner") == uid]

    def save_product(self, pid, p, owner=None):
        fields = {
            "name": str(p.get("name", "")).strip(),
            "price_usd": float(p.get("price_usd", 0)),
            "description": str(p.get("description", "")),
            "quantity": int(p.get("quantity", -1)),
            "city": str(p.get("city") or "tbilisi"),
            "districts": [str(x) for x in p.get("districts", [])],
        }
        if self.supports_categories:
            fields["category"] = str(p.get("category") or "")
        if "weight" in p:       # short tag shown on the website ("2.0")
            fields["weight"] = str(p.get("weight") or "").strip()[:20]
        if "image" in p:        # website picture, from the bot's library
            img = str(p.get("image") or "")
            if img and (os.path.basename(img) != img or not (self.dir / img).is_file()):
                raise AdapterError("that picture is not in the bot's picture library")
            fields["image"] = img
        if "variants" in p:     # which variants the buyer can pick: land (buried in district) / building
            v = [k for k in ("land", "building") if k in (p.get("variants") or [])]
            if not v:
                raise AdapterError("pick at least one variant")
            fields["variants"] = v
        if "delivery_text" in p or "delivery_image" in p:   # what the buyer gets after buying with balance
            fields["delivery_text"] = str(p.get("delivery_text") or "")[:3500]
            image = str(p.get("delivery_image") or "")
            if image:
                ext = os.path.splitext(image)[1].lower()
                if os.path.basename(image) != image or ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm", ".m4v") \
                        or not (self.dir / image).is_file():
                    raise AdapterError("that delivery picture is not in the bot's picture library")
            fields["delivery_image"] = image
        if not fields["name"]:
            raise AdapterError("name is required")
        if owner is not None:
            fields["owner"] = int(owner)
            pid = pid or f"product:u{int(owner)}_{uuid.uuid4().hex[:6]}"   # short: callback_data limit is 64 bytes
        pid = pid or "product:" + uuid.uuid4().hex[:10]

        def fn(d):
            if owner is not None:
                d.setdefault("private_listings", {}).setdefault(str(int(owner)), {"created": now_str()[:16]})
            d.setdefault("products", {}).setdefault(pid, {}).update(fields)
        self._mutate(fn)
        return pid

    def delete_product(self, pid):
        return self._mutate(lambda d: d.get("products", {}).pop(pid, None) is not None)

    def add_district(self, name, city="tbilisi"):
        slug = "district:" + uuid.uuid4().hex[:8]
        self._mutate(lambda d: d.setdefault("districts", {}).__setitem__(slug, {"name": name, "city": city, "image": ""}))
        return slug

    def set_district_image(self, slug, image):
        image = str(image or "")
        if image:
            ext = os.path.splitext(image)[1].lower()
            if os.path.basename(image) != image or ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm", ".m4v") \
                    or not (self.dir / image).is_file():
                raise AdapterError("that picture is not in the bot's picture library")

        def fn(d):
            if slug not in d.get("districts", {}):
                raise AdapterError("district not found")
            d["districts"][slug]["image"] = image
        self._mutate(fn)

    def delete_district(self, slug):
        def fn(d):
            ok = d.get("districts", {}).pop(slug, None) is not None
            for p in d.get("products", {}).values():
                if slug in p.get("districts", []):
                    p["districts"].remove(slug)
            return ok
        return self._mutate(fn)

    # website (the storefront in /opt/bot-site shares this bot's data.json)
    @property
    def supports_site(self) -> bool:
        return self.name == "test2" and Path("/opt/bot-site/webapp.py").exists()

    _SITE_DEFAULT = {"enabled": True, "brand": "МАГАЗИН: CAPONE.CC", "name": "CAPONE.CC", "copyright": "© 2026 · Все права защищены",
                     "coin": "LTC", "default_image": "", "city_images": {}, "links": []}

    def site_get(self):
        d = self._load()
        s = dict(self._SITE_DEFAULT)
        if not d.get("site"):
            import importlib.util
            try:
                spec = importlib.util.spec_from_file_location("_site_defaults", "/opt/bot-site/site_defaults.py")
                m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); s["links"] = m.LINKS
            except Exception:
                pass
        s.update({k: v for k, v in (d.get("site") or {}).items() if k in s})
        orders = {}
        for pr in d.get("purchases", []):
            if pr.get("web_user"):
                orders[pr["web_user"].lower()] = orders.get(pr["web_user"].lower(), 0) + 1
        users = [{"login": u.get("login", k), "balance": float(u.get("balance", 0)), "banned": bool(u.get("banned")),
                  "created": u.get("created", ""), "orders": orders.get(k, 0)} for k, u in d.get("site_users", {}).items()]
        revs = sorted(d.get("site_reviews", []), key=lambda r: (r.get("approved", False), -r.get("ts", 0)))
        return {"site": s, "users": users, "reviews": revs,
                "cities": sorted({str(x.get("city") or "tbilisi").lower() for x in d.get("districts", {}).values()}
                                 | {str(x.get("city") or "tbilisi").lower() for x in d.get("products", {}).values()}),
                "coins": [c["code"] for c in (json.loads((self.dir / "menu_config.json").read_text(encoding="utf-8")).get("payments", {}).get("coins") or [])] or ["BTC", "LTC", "USDT"]}

    def _site_mutate(self, fn):
        with open(self.lock_path, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            d = self._load()
            out = fn(d)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            return out

    def site_set(self, s):
        def pic(v):
            v = str(v or "")
            if v and (os.path.basename(v) != v or not (self.dir / v).is_file()):
                raise AdapterError(f"picture '{v}' is not in the bot's picture library")
            return v
        coin = re.sub(r"[^A-Za-z0-9]", "", str(s.get("coin") or "LTC")).upper()[:10] or "LTC"
        links = []
        for g in (s.get("links") or [])[:8]:
            title = str(g.get("title") or "").strip()[:80]
            items = [str(i).strip()[:200] for i in (g.get("items") or []) if str(i).strip()][:10]
            if title and items:
                links.append({"title": title, "icon": str(g.get("icon") or "↗")[:2], "items": items})
        clean = {"enabled": bool(s.get("enabled", True)),
                 "brand": str(s.get("brand") or "").strip()[:60] or "SHOP", "name": str(s.get("name") or "").strip()[:40] or "SHOP",
                 "copyright": str(s.get("copyright") or "").strip()[:120], "coin": coin, "default_image": pic(s.get("default_image")),
                 "city_images": {str(c).lower()[:40]: pic(v) for c, v in (s.get("city_images") or {}).items() if v}, "links": links}
        self._site_mutate(lambda d: d.__setitem__("site", clean))

    def site_user_action(self, login, action, value=None):
        key = str(login or "").lower()

        def fn(d):
            users = d.get("site_users", {})
            u = users.get(key)
            if not u:
                raise AdapterError("website user not found")
            if action == "balance":
                u["balance"] = round(float(value), 2)
                if u["balance"] < 0:
                    raise AdapterError("balance cannot be negative")
            elif action == "ban":
                u["banned"] = bool(value)
            elif action == "password":
                from werkzeug.security import generate_password_hash
                if len(str(value or "")) < 6:
                    raise AdapterError("password must be at least 6 characters")
                u["pw"] = generate_password_hash(str(value))
            elif action == "delete":
                users.pop(key)
            else:
                raise AdapterError("unknown action")
        self._site_mutate(fn)

    def site_review_action(self, rid, action, review=None):
        def fn(d):
            revs = d.setdefault("site_reviews", [])
            if action == "add":
                text = str((review or {}).get("text") or "").strip()[:600]
                login = str((review or {}).get("login") or "").strip()[:32]
                if not text or not login:
                    raise AdapterError("login and text are required")
                revs.append({"id": max([r.get("id", 0) for r in revs] + [0]) + 1, "login": login,
                             "city": str(review.get("city") or "")[:40], "district": str(review.get("district") or "")[:60],
                             "product": str(review.get("product") or "")[:120], "text": text, "purchase": bool(review.get("purchase", True)),
                             "approved": True, "date": datetime.now().strftime("%d.%m.%Y · %H:%M"), "ts": time.time()})
                return
            r = next((x for x in revs if x.get("id") == rid), None)
            if not r:
                raise AdapterError("review not found")
            if action == "approve":
                r["approved"] = True
            elif action == "hide":
                r["approved"] = False
            elif action == "delete":
                revs.remove(r)
        self._site_mutate(fn)

    # admin notifications (menu_config.json -> notifications: {admin id: {event: false}})
    NOTIFY_EVENTS = [("new_user", "New user started the bot"),
                     ("topup_started", "Top-up initiated (user opened a deposit address)"),
                     ("deposit_received", "Deposit detected on the wallet"),
                     ("purchase", "Product bought with balance"),
                     ("paid_pressed", "User pressed “I paid”"),
                     ("site_user", "New website user registered")]

    @property
    def supports_notifications(self) -> bool:
        return (self.dir / "notify.py").exists() and (self.dir / "menu_config.json").exists()

    def notifications(self):
        try:
            cfg = json.loads((self.dir / "menu_config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cfg = {}
        raw = parse_env(self.dir / ".env").get("ADMIN_IDS", "")
        ids = [x.strip() for x in raw.split(",") if x.strip().isdigit()]
        sec = cfg.get("notifications") or {}
        return {"events": [{"id": k, "label": v} for k, v in self.NOTIFY_EVENTS],
                "admins": [{"id": i, "off": [k for k, _ in self.NOTIFY_EVENTS if (sec.get(i) or {}).get(k) is False]} for i in ids]}

    def set_notifications(self, admin_id, off):
        known = {k for k, _ in self.NOTIFY_EVENTS}
        if admin_id not in {a["id"] for a in self.notifications()["admins"]}:
            raise AdapterError("that user id is not one of this bot's admins")
        off = [k for k in off if k in known]
        path = self.dir / "menu_config.json"
        from constructor import _backup_config, _bot_or_none
        bot = _bot_or_none(self.name)
        with open(self.lock_path, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            cfg = json.loads(path.read_text(encoding="utf-8"))
            if bot:
                _backup_config(bot)
            sec = cfg.setdefault("notifications", {})
            if off:
                sec[admin_id] = {k: False for k in off}
            else:
                sec.pop(admin_id, None)
            if not sec:
                cfg.pop("notifications", None)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)

    # currency (menu_config.json -> currency, shown by the bot and used for crypto conversion)
    @property
    def supports_currency(self) -> bool:
        try:
            return "def set_currency" in (self.dir / "settings_store.py").read_text()
        except OSError:
            return False

    def currency(self):
        try:
            cfg = json.loads((self.dir / "menu_config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return currency_util.clean_currency(cfg.get("currency"))[2]

    def set_currency(self, cur):
        path = self.dir / "menu_config.json"
        if not path.exists():
            raise AdapterError("this bot has no menu_config.json")
        from constructor import _backup_config, _bot_or_none
        bot = _bot_or_none(self.name)
        with open(self.lock_path, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            cfg = json.loads(path.read_text(encoding="utf-8"))
            if bot:
                _backup_config(bot)
            if cur:
                cfg["currency"] = cur
            else:
                cfg.pop("currency", None)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)

    # categories (city -> category -> listing flow)
    @property
    def supports_categories(self) -> bool:
        try:
            return "def add_category" in (self.dir / "data_store.py").read_text()
        except OSError:
            return False

    def add_category(self, name, city=""):
        slug = "category:" + uuid.uuid4().hex[:6]
        self._mutate(lambda d: d.setdefault("categories", {}).__setitem__(slug, {"name": name, "city": city or "", "image": ""}))
        return slug

    def delete_category(self, slug):
        def fn(d):
            ok = d.get("categories", {}).pop(slug, None) is not None
            for p in d.get("products", {}).values():
                if p.get("category") == slug:
                    p["category"] = ""
            return ok
        return self._mutate(fn)

    # purchase history (shown to the user by the bot's "history" page)
    @property
    def supports_purchases(self) -> bool:
        try:
            return "def add_purchase" in (self.dir / "data_store.py").read_text()
        except OSError:
            return False

    def purchases(self, uid):
        items = [p for p in self._load().get("purchases", []) if p.get("user_id") == uid]
        return sorted(items, key=lambda p: p.get("id", 0), reverse=True)

    def add_purchase(self, uid, product, price, note=""):
        product = str(product).strip()[:200]
        if not product:
            raise AdapterError("product name is required")

        def fn(d):
            if str(uid) not in d.get("users", {}):
                raise AdapterError("user not found")
            items = d.setdefault("purchases", [])
            pid = max([p.get("id", 0) for p in items] + [0]) + 1
            items.append({"id": pid, "user_id": uid, "product": product, "price_usd": float(price or 0),
                          "note": str(note or "")[:300], "date": now_str()[:16], "by": "panel"})
            self._referral_bonus(d, uid, float(price or 0))
            return pid
        return self._mutate(fn)

    def _referral_bonus(self, d, uid, price):
        """The buyer's inviter earns the bot's referral percentage (menu_config.json -> referral)."""
        try:
            cfg = json.loads((self.dir / "menu_config.json").read_text(encoding="utf-8"))
            ref = cfg.get("referral") or {}
            pct = float(ref.get("percent", 2)) if ref.get("enabled") else 0.0
        except (OSError, ValueError, TypeError):
            return
        u = d.get("users", {}).get(str(uid)) or {}
        r = d.get("users", {}).get(str(u.get("referrer"))) if u.get("referrer") else None
        bonus = round(price * max(0.0, min(pct, 100.0)) / 100.0, 2)
        if r is not None and bonus > 0:
            r["balance"] = round(float(r.get("balance", 0) or 0) + bonus, 2)
            r["ref_earned"] = round(float(r.get("ref_earned", 0) or 0) + bonus, 2)

    def delete_purchase(self, pid):
        def fn(d):
            before = len(d.get("purchases", []))
            d["purchases"] = [p for p in d.get("purchases", []) if p.get("id") != pid]
            return len(d["purchases"]) < before
        return self._mutate(fn)

    # bot-native scheduled announcements (the bot's own scheduler sends these)
    def native_schedules(self):
        return [{"id": a["id"], "text": a.get("text", ""), "run_at": a.get("run_at", ""),
                 "recurring": a.get("recurring"), "photo": bool(a.get("photo"))}
                for a in self._load().get("announcements_scheduled", []) if not a.get("sent")]

    def delete_native_schedule(self, sid):
        def fn(d):
            before = len(d.get("announcements_scheduled", []))
            d["announcements_scheduled"] = [a for a in d.get("announcements_scheduled", []) if a["id"] != sid]
            return len(d["announcements_scheduled"]) < before
        return self._mutate(fn)


class SqlAdapter:
    """users table lives in a sqlite file; 'shop' has balances/catalog, 'simple' only ids."""

    def __init__(self, name, bot_dir: Path, path: Path, kind: str):
        self.name, self.dir, self.path, self.kind = name, bot_dir, path, kind
        self.tables = _tables(path)
        self.ucols = self.tables["users"]
        self.idcol = "tg_user_id" if kind == "shop" else "user_id"
        self.has_balance = "balance_usd" in self.ucols
        self.has_ban = "blocked" in self.ucols
        self.has_products = "catalog_products" in self.tables
        self.has_delivery_image = "delivery_image" in self.tables.get("catalog_products", [])
        # private listings need the bot's updated code to have created these
        self.supports_private = ("private_listings" in self.tables
                                 and "owner_user_id" in self.tables.get("catalog_products", []))

    def con(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def backup(self) -> str:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dest = BACKUP_DIR / f"global-clean-{self.name}-{utcnow().strftime('%Y%m%d-%H%M%S')}{self.path.suffix}"
        with closing(self.con()) as src, closing(sqlite3.connect(dest)) as dst:
            src.backup(dst)
        return str(dest)

    def users(self):
        joined_col = next((c for c in ("created_at", "joined_at", "started_at") if c in self.ucols), None)
        sel = [f"{self.idcol} AS id"]
        sel.append("username" if "username" in self.ucols else "'' AS username")
        sel.append("balance_usd AS balance" if self.has_balance else "0 AS balance")
        sel.append(f"{joined_col} AS joined" if joined_col else "'' AS joined")
        sel.append("blocked AS banned" if self.has_ban else "0 AS banned")
        sel.append("is_bot" if "is_bot" in self.ucols else "0 AS is_bot")
        with closing(self.con()) as c:
            rows = c.execute(f"SELECT {', '.join(sel)} FROM users").fetchall()
        return [{"id": r["id"], "username": r["username"] or "", "balance": float(r["balance"] or 0),
                 "joined": r["joined"] or "", "banned": bool(r["banned"]), "is_bot": bool(r["is_bot"])}
                for r in rows]

    def set_ban(self, uid, banned):
        if not self.has_ban:
            raise AdapterError("this bot has no ban flag")
        with closing(self.con()) as c, c:
            c.execute(f"UPDATE users SET blocked=? WHERE {self.idcol}=?", (int(bool(banned)), uid))

    def set_balance(self, uid, mode, amount):
        if not self.has_balance:
            raise AdapterError("this bot has no balances")
        with closing(self.con()) as c, c:
            row = c.execute(f"SELECT balance_usd FROM users WHERE {self.idcol}=?", (uid,)).fetchone()
            if not row:
                raise AdapterError("user not found")
            new = amount if mode == "set" else float(row[0] or 0) + amount
            new = round(max(new, 0.0), 2)
            c.execute(f"UPDATE users SET balance_usd=? WHERE {self.idcol}=?", (new, uid))
            return new

    def delete_users(self, ids):
        with closing(self.con()) as c, c:
            n = 0
            for i in ids:
                n += c.execute(f"DELETE FROM users WHERE {self.idcol}=?", (i,)).rowcount
            return n

    # catalog
    def meta(self):
        with closing(self.con()) as c:
            cats = [dict(r) for r in c.execute("SELECT * FROM catalog_categories ORDER BY name")] \
                if "catalog_categories" in self.tables else []
            cities = sorted({r[0] for r in c.execute("SELECT DISTINCT city FROM catalog_products")}) \
                if "city" in self.tables.get("catalog_products", []) else []
        # city values the bot understands: infer the naming from the column default
        with closing(self.con()) as c:
            dflt = next((r[4] for r in c.execute("PRAGMA table_info(catalog_products)") if r[1] == "city"), None)
        city_options = [] if dflt is None else (["tbilisi", "batumi"] if "tbilisi" in str(dflt) else ["თბილისი", "ბათუმი"])
        return {"fields": ["name", "price_usd", "description", "delivery_text", "category", "district", "city"],
                "has_delivery_image": self.has_delivery_image, "city_options": city_options,
                "categories": [{"id": x["category_id"], "name": x["name"], "city": x.get("city", "")} for x in cats],
                "cities": cities}

    def products(self):
        with closing(self.con()) as c:
            cats = {r["category_id"]: r["name"] for r in c.execute("SELECT category_id, name FROM catalog_categories")}
            where = "WHERE owner_user_id IS NULL" if self.supports_private else ""
            rows = [dict(r) for r in c.execute(f"SELECT * FROM catalog_products {where} ORDER BY created_at DESC")]
        for r in rows:
            r["id"] = r["product_id"]
            r["category_name"] = cats.get(r.get("category"), r.get("category"))
        return rows

    def list_private(self):
        with closing(self.con()) as c:
            return [dict(r) for r in c.execute(
                "SELECT l.user_id, (SELECT COUNT(*) FROM catalog_products p WHERE p.owner_user_id = l.user_id) AS products "
                "FROM private_listings l ORDER BY l.created_at DESC")]

    def create_private(self, uid):
        with closing(self.con()) as c, c:
            c.execute("INSERT OR IGNORE INTO private_listings (user_id) VALUES (?)", (uid,))

    def delete_private(self, uid):
        with closing(self.con()) as c, c:
            c.execute("DELETE FROM product_districts WHERE product_id IN "
                      "(SELECT product_id FROM catalog_products WHERE owner_user_id = ?)", (uid,))
            c.execute("DELETE FROM catalog_products WHERE owner_user_id = ?", (uid,))
            c.execute("DELETE FROM private_listings WHERE user_id = ?", (uid,))

    def private_products(self, uid):
        with closing(self.con()) as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM catalog_products WHERE owner_user_id = ? ORDER BY created_at DESC", (uid,))]
            cats = {x[0]: x[1] for x in c.execute("SELECT category_id, name FROM catalog_categories")}
            for r in rows:
                r["id"] = r["product_id"]
                r["category_name"] = cats.get(r.get("category"), r.get("category"))
                r["districts"] = [x[0] for x in c.execute(
                    "SELECT name FROM product_districts WHERE product_id = ? ORDER BY name", (r["product_id"],))]
        return rows

    def save_product(self, pid, p, owner=None):
        name = str(p.get("name", "")).strip()
        if not name:
            raise AdapterError("name is required")
        has_city = "city" in self.tables["catalog_products"]
        vals = {
            "name": name, "price_usd": float(p.get("price_usd", 0)),
            "description": str(p.get("description", "")), "delivery_text": str(p.get("delivery_text", "")),
            "category": str(p.get("category", "")), "district": str(p.get("district", "")),
        }
        names = [str(x).strip() for x in (p.get("districts") or []) if str(x).strip()]
        if names:
            vals["district"] = names[0]
        elif vals["district"]:
            names = [vals["district"]]
        if has_city:
            vals["city"] = str(p.get("city", ""))
        if self.has_delivery_image and "delivery_image" in p:
            vals["delivery_image"] = Path(str(p.get("delivery_image") or "")).name
        with closing(self.con()) as c, c:
            if pid:
                sets = ", ".join(f"{k}=?" for k in vals)
                c.execute(f"UPDATE catalog_products SET {sets} WHERE product_id=?", (*vals.values(), pid))
            else:
                pid = "admin_" + uuid.uuid4().hex
                if owner is not None:
                    vals["owner_user_id"] = int(owner)
                cols = ", ".join(["product_id", *vals])
                c.execute(f"INSERT INTO catalog_products ({cols}) VALUES ({', '.join('?' * (len(vals) + 1))})",
                          (pid, *vals.values()))
                if owner is not None:
                    c.execute("INSERT OR IGNORE INTO private_listings (user_id) VALUES (?)", (int(owner),))
            if "product_districts" in self.tables:
                c.execute("DELETE FROM product_districts WHERE product_id=?", (pid,))
                for n in names:
                    c.execute("INSERT OR IGNORE INTO product_districts (product_id, name) VALUES (?, ?)", (pid, n))
        return pid

    def delete_product(self, pid):
        with closing(self.con()) as c, c:
            if "product_districts" in self.tables:
                c.execute("DELETE FROM product_districts WHERE product_id=?", (pid,))
            return c.execute("DELETE FROM catalog_products WHERE product_id=?", (pid,)).rowcount > 0

    def add_category(self, name, city=""):
        cid = "category_" + uuid.uuid4().hex
        with closing(self.con()) as c, c:
            if "city" in self.tables["catalog_categories"]:
                c.execute("INSERT INTO catalog_categories (category_id, name, city) VALUES (?,?,?)", (cid, name, city))
            else:
                c.execute("INSERT INTO catalog_categories (category_id, name) VALUES (?,?)", (cid, name))
        return cid

    def delete_category(self, cid):
        with closing(self.con()) as c, c:
            if c.execute("SELECT 1 FROM catalog_products WHERE category=? LIMIT 1", (cid,)).fetchone():
                raise AdapterError("category still has products")
            return c.execute("DELETE FROM catalog_categories WHERE category_id=?", (cid,)).rowcount > 0


def get_adapter(name: str):
    cfg = load_registry().get(name)
    if not cfg or name in SKIP_BOTS:
        return None
    bot_dir = Path(cfg["dir"])
    dj = bot_dir / "data.json"
    if dj.exists():
        try:
            if isinstance(json.loads(dj.read_text(encoding="utf-8")).get("users"), dict):
                return JsonAdapter(name, bot_dir)
        except (OSError, ValueError):
            pass
    for path in _sqlite_candidates(bot_dir):
        try:
            t = _tables(path)
        except sqlite3.Error:
            continue
        cols = t.get("users", [])
        if "tg_user_id" in cols:
            return SqlAdapter(name, bot_dir, path, "shop")
        if "user_id" in cols:
            return SqlAdapter(name, bot_dir, path, "simple")
    return None


def need_adapter(bot):
    ad = get_adapter(bot)
    if not ad:
        raise AdapterError(f"bot '{bot}' has no recognised user database")
    return ad


def api(fn):
    @wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except AdapterError as e:
            return jsonify(error=str(e)), 400
        except (ValueError, TypeError, KeyError) as e:
            return jsonify(error=f"bad request: {e}"), 400
        except (sqlite3.Error, OSError) as e:
            return jsonify(error=f"storage error: {e}"), 500
    return wrapper


# ── Telegram client (stdlib only) ──────────────────────────────────────────
_rate_lock = threading.Lock()
_rate_next: dict[str, float] = {}
RATE_PER_SEC = 20          # per bot token; Telegram allows ~30/s


def _throttle(token: str):
    with _rate_lock:
        now = time.monotonic()
        nxt = _rate_next.get(token, 0.0)
        wait = nxt - now
        _rate_next[token] = max(now, nxt) + 1.0 / RATE_PER_SEC
    if wait > 0:
        time.sleep(wait)


def tg_call(token: str, method: str, payload: dict | None = None, files: dict | None = None):
    """Returns (ok, result_or_description, error_code)."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    for attempt in range(4):
        _throttle(token)
        if files:
            boundary = uuid.uuid4().hex
            body = b""
            for k, v in (payload or {}).items():
                body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
            for k, (fname, data) in files.items():
                ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
                body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                         f'Content-Type: {ctype}\r\n\r\n').encode() + data + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        else:
            req = urllib.request.Request(url, json.dumps(payload or {}).encode(),
                                         {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.loads(r.read())
                return True, j.get("result"), 200
        except urllib.error.HTTPError as e:
            try:
                j = json.loads(e.read())
            except ValueError:
                j = {}
            desc = j.get("description", str(e))
            if e.code == 429:
                time.sleep(int(j.get("parameters", {}).get("retry_after", 2)) + 1)
                continue
            return False, desc, e.code
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == 3:
                return False, f"network: {e}", 0
            time.sleep(1 + attempt)
    return False, "rate limited", 429


def classify(code: int, desc: str) -> str | None:
    """Permanent delivery failure reason, or None for transient/unknown errors."""
    d = (desc or "").lower()
    if "blocked by the user" in d:
        return "blocked"
    if "deactivated" in d:
        return "deleted"
    if "chat not found" in d or "user not found" in d or "peer_id_invalid" in d:
        return "not_found"
    if code == 403 and "kicked" in d:
        return "blocked"
    if "can't initiate" in d or "cannot initiate" in d:
        return "not_found"   # this token never talked to the user
    return None


_token_names: dict[str, str] = {}


def valid_tokens(tokens: list[str]) -> list[str]:
    """Keep only tokens Telegram accepts (bot .env files may hold revoked ones)."""
    good = []
    for t in dict.fromkeys(tokens):
        ok, me, code = tg_call(t, "getMe")
        if ok:
            good.append(t)
            _token_names[t] = "@" + (me or {}).get("username", "?")
    if not good:
        raise AdapterError("none of this bot's tokens are accepted by Telegram")
    return good


def probe_user(tokens: list[str], uid: int, want_profile: bool):
    """Try every token. Returns (status, reason, profile) with status alive|dead|unknown."""
    dead_reason, unknown, profile = None, False, {}
    for tok in tokens:
        ok, res, code = tg_call(tok, "sendChatAction", {"chat_id": uid, "action": "typing"})
        if ok:
            if want_profile:
                ok2, chat, _ = tg_call(tok, "getChat", {"chat_id": uid})
                if ok2 and isinstance(chat, dict):
                    profile = {"username": chat.get("username") or "", "first_name": chat.get("first_name") or ""}
            return "alive", "", profile
        reason = classify(code, res)
        if reason:
            dead_reason = dead_reason or reason
        else:
            unknown = True
    if unknown or not dead_reason:
        return "unknown", "", profile
    return "dead", dead_reason, profile


# ── jobs ───────────────────────────────────────────────────────────────────
_cancel_flags: set[int] = set()


def job_create(bot, kind, total, note="") -> int:
    with closing(pdb()) as con, con:
        cur = con.execute("INSERT INTO jobs (bot, kind, status, total, note, started) VALUES (?,?,?,?,?,?)",
                          (bot, kind, "running", total, note, now_str()))
        return cur.lastrowid


def job_update(jid, **kw):
    sets = ", ".join(f"{k}=?" for k in kw)
    with closing(pdb()) as con, con:
        con.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*kw.values(), jid))


def job_running(bot) -> bool:
    with closing(pdb()) as con:
        return con.execute("SELECT 1 FROM jobs WHERE bot=? AND status IN ('running','cancelling') LIMIT 1",
                           (bot,)).fetchone() is not None


def job_is_cancelled(jid) -> bool:
    with closing(pdb()) as con:
        r = con.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
        return bool(r) and r["status"] == "cancelling"


def run_in_pool(jid, items, worker, workers=8):
    """worker(item) -> 'ok'|'dead'|'failed'. Persists counters as it goes."""
    counts = {"ok": 0, "dead": 0, "failed": 0, "done": 0}
    lock = threading.Lock()
    stop = threading.Event()

    def task(item):
        if stop.is_set():
            return
        res = worker(item)
        with lock:
            counts[res] += 1
            counts["done"] += 1
            if counts["done"] % 20 == 0:
                job_update(jid, **counts)
                if job_is_cancelled(jid):
                    stop.set()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(task, items))
    job_update(jid, **counts)
    return counts, stop.is_set()


def set_status(bot, uid, status, reason="", profile=None):
    profile = profile or {}
    with closing(pdb()) as con, con:
        con.execute(
            "INSERT INTO user_status (bot, uid, status, reason, username, first_name, checked_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(bot, uid) DO UPDATE SET status=excluded.status, "
            "reason=excluded.reason, checked_at=excluded.checked_at, "
            "username=COALESCE(NULLIF(excluded.username,''), user_status.username), "
            "first_name=COALESCE(NULLIF(excluded.first_name,''), user_status.first_name)",
            (bot, uid, status, reason, profile.get("username", ""), profile.get("first_name", ""), now_str()))


def status_map(bot) -> dict[int, dict]:
    with closing(pdb()) as con:
        return {r["uid"]: dict(r) for r in con.execute("SELECT * FROM user_status WHERE bot=?", (bot,))}


def start_scan(bot, want_profile) -> int:
    ad = need_adapter(bot)
    tokens = valid_tokens(bot_tokens(ad.dir))
    ids = [u["id"] for u in ad.users()]
    jid = job_create(bot, "scan", len(ids), "usernames+scan" if want_profile else "scan")

    def run():
        def worker(uid):
            st, reason, prof = probe_user(tokens, uid, want_profile)
            if st == "unknown":
                return "failed"
            set_status(bot, uid, st, reason, prof)
            return "dead" if st == "dead" else "ok"
        try:
            _, cancelled = run_in_pool(jid, ids, worker)
            job_update(jid, status="cancelled" if cancelled else "done", finished=now_str())
        except Exception as e:  # noqa: BLE001
            job_update(jid, status="error", note=str(e)[:300], finished=now_str())

    threading.Thread(target=run, daemon=True).start()
    return jid


def start_broadcast(bot, text, photo_path, html, skip_banned=True) -> int:
    ad = need_adapter(bot)
    tokens = valid_tokens(bot_tokens(ad.dir))
    st = status_map(bot)
    ids = [u["id"] for u in ad.users()
           if not (st.get(u["id"], {}).get("status") == "dead")
           and not (skip_banned and u["banned"]) and not u.get("is_bot")]
    jid = job_create(bot, "broadcast", len(ids), (text[:80] or "photo"))
    photo_bytes = Path(photo_path).read_bytes() if photo_path else None

    def send(tok, uid):
        base = {"chat_id": uid, "disable_notification": "true"}   # silent push, no sound/vibration
        if html:
            base["parse_mode"] = "HTML"
        if photo_bytes:
            base.update(caption=text)
            return tg_call(tok, "sendPhoto", base, {"photo": (Path(photo_path).name, photo_bytes)})
        base.update(text=text, disable_web_page_preview="true")
        return tg_call(tok, "sendMessage", base)

    per_bot = {t: 0 for t in tokens}
    per_bot_lock = threading.Lock()

    def run():
        def worker(uid):
            # Send through EVERY bot token: each bot the user started delivers its own copy.
            # Tokens the user never started / has blocked fail permanently and are simply skipped.
            delivered, dead_reason, other_error = 0, None, False
            for tok in tokens:
                ok, res, code = send(tok, uid)
                if ok:
                    delivered += 1
                    with per_bot_lock:
                        per_bot[tok] += 1
                    continue
                reason = classify(code, res)
                if reason:
                    dead_reason = dead_reason or reason
                else:
                    other_error = True
            if delivered:
                return "ok"
            if other_error:
                return "failed"
            if dead_reason:
                set_status(bot, uid, "dead", dead_reason)
                return "dead"
            return "failed"
        try:
            _, cancelled = run_in_pool(jid, ids, worker, workers=16)
            summary = ", ".join(f"{_token_names.get(t, '?')}: {n}" for t, n in per_bot.items())
            job_update(jid, status="cancelled" if cancelled else "done", finished=now_str(),
                       note=f"delivered per bot — {summary}"[:300])
        except Exception as e:  # noqa: BLE001
            job_update(jid, status="error", note=str(e)[:300], finished=now_str())

    threading.Thread(target=run, daemon=True).start()
    return jid


# ── scheduler (one worker process wins the file lock) ──────────────────────
_sched_lock_fh = None


def _scheduler_loop():
    with closing(pdb()) as con, con:
        con.execute("UPDATE jobs SET status='interrupted', finished=? WHERE status IN ('running','cancelling')",
                    (now_str(),))
    while True:
        try:
            with closing(pdb()) as con:
                due = [dict(r) for r in con.execute("SELECT * FROM schedules WHERE run_at <= ?", (now_str(),))]
            for s in due:
                with closing(pdb()) as con, con:
                    if s["every_min"]:
                        nxt = (utcnow() + timedelta(minutes=s["every_min"])).strftime(TIME_FMT)
                        con.execute("UPDATE schedules SET run_at=? WHERE id=?", (nxt, s["id"]))
                    else:
                        con.execute("DELETE FROM schedules WHERE id=?", (s["id"],))
                try:
                    if not job_running(s["bot"]):
                        start_broadcast(s["bot"], s["text"], s["photo"], bool(s["html"]))
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        time.sleep(30)


def _start_scheduler_once():
    global _sched_lock_fh
    try:
        fh = open(STATE_DIR / "scheduler.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    _sched_lock_fh = fh
    threading.Thread(target=_scheduler_loop, daemon=True).start()


_start_scheduler_once()


# ── routes ─────────────────────────────────────────────────────────────────
@global_bp.route("/global")
@login_required
def global_page():
    return render_template("global_admin.html")


@global_bp.route("/global/api/bots")
@api
def api_bots():
    out = []
    for name, cfg in load_registry().items():
        if name in SKIP_BOTS:
            continue
        item = {"name": name, "display": cfg.get("display_name", name), "supported": False}
        try:
            ad = get_adapter(name)
            if ad:
                users = ad.users()
                st = status_map(name)
                item.update(
                    supported=True, kind=ad.kind, users=len(users),
                    dead=sum(1 for u in users if st.get(u["id"], {}).get("status") == "dead"),
                    scanned=sum(1 for u in users if u["id"] in st),
                    balance=round(sum(u["balance"] for u in users), 2),
                    has_products=ad.has_products, has_balance=ad.has_balance, has_ban=ad.has_ban,
                    has_private=bool(getattr(ad, "supports_private", False)),
                    busy=job_running(name),
                    can_notify=bool(getattr(ad, "supports_notifications", False)),
                    can_site=bool(getattr(ad, "supports_site", False)),
                    can_currency=bool(getattr(ad, "supports_currency", False)),
                    currency=ad.currency() if getattr(ad, "supports_currency", False) else None,
                )
        except Exception as e:  # noqa: BLE001
            item["error"] = str(e)
        out.append(item)
    return jsonify(bots=out)


@global_bp.route("/global/api/<bot>/users")
@api
def api_users(bot):
    ad = need_adapter(bot)
    st = status_map(bot)
    q = request.args.get("q", "").strip().lower().lstrip("@")
    flt = request.args.get("filter", "all")
    sort = request.args.get("sort", "joined")
    desc = request.args.get("dir", "desc") == "desc"
    page = max(int(request.args.get("page", 1)), 1)
    per = min(max(int(request.args.get("per", 50)), 10), 500)

    rows = []
    for u in ad.users():
        s = st.get(u["id"], {})
        u["username"] = u["username"] or s.get("username") or ""
        u["first_name"] = s.get("first_name") or ""
        u["state"] = "dead" if s.get("status") == "dead" else ("alive" if s.get("status") == "alive" else "unchecked")
        u["reason"] = s.get("reason") or ""
        u["checked_at"] = s.get("checked_at") or ""
        rows.append(u)

    totals = {"count": len(rows), "balance": round(sum(r["balance"] for r in rows), 2),
              "dead": sum(r["state"] == "dead" for r in rows),
              "alive": sum(r["state"] == "alive" for r in rows),
              "unchecked": sum(r["state"] == "unchecked" for r in rows),
              "banned": sum(r["banned"] for r in rows)}

    if q:
        rows = [r for r in rows if q in str(r["id"]) or q in r["username"].lower() or q in r["first_name"].lower()]
    if flt == "dead":
        rows = [r for r in rows if r["state"] == "dead"]
    elif flt == "alive":
        rows = [r for r in rows if r["state"] == "alive"]
    elif flt == "unchecked":
        rows = [r for r in rows if r["state"] == "unchecked"]
    elif flt == "banned":
        rows = [r for r in rows if r["banned"]]
    elif flt == "balance":
        rows = [r for r in rows if r["balance"] > 0]

    keymap = {"id": lambda r: r["id"], "username": lambda r: r["username"].lower(),
              "balance": lambda r: r["balance"], "joined": lambda r: r["joined"], "state": lambda r: r["state"]}
    rows.sort(key=keymap.get(sort, keymap["joined"]), reverse=desc)
    total = len(rows)
    rows = rows[(page - 1) * per: page * per]
    return jsonify(users=rows, total=total, page=page, per=per, totals=totals,
                   caps={"balance": ad.has_balance, "ban": ad.has_ban,
                         "purchases": bool(getattr(ad, "supports_purchases", False))})


@global_bp.route("/global/api/<bot>/users/<int:uid>/ban", methods=["POST"])
@api
def api_ban(bot, uid):
    need_adapter(bot).set_ban(uid, bool((request.get_json() or {}).get("banned")))
    return jsonify(ok=True)


@global_bp.route("/global/api/<bot>/users/<int:uid>/balance", methods=["POST"])
@api
def api_balance(bot, uid):
    j = request.get_json() or {}
    mode = j.get("mode", "add")
    if mode not in ("add", "set"):
        raise ValueError("mode")
    new = need_adapter(bot).set_balance(uid, mode, float(j["amount"]))
    return jsonify(ok=True, balance=new)


# district pictures
@global_bp.route("/global/api/<bot>/districts/image", methods=["POST"])
@api
def api_district_image(bot):
    ad = need_adapter(bot)
    if ad.kind != "json":
        raise AdapterError("this bot has no district pictures")
    j = request.get_json() or {}
    ad.set_district_image(str(j.get("id", "")), j.get("image", ""))
    return jsonify(ok=True)


# website
def _site_ad(bot):
    ad = need_adapter(bot)
    if not getattr(ad, "supports_site", False):
        raise AdapterError("this bot has no website")
    return ad


@global_bp.route("/global/api/<bot>/site")
@api
def api_site_get(bot):
    return jsonify(ok=True, **_site_ad(bot).site_get())


@global_bp.route("/global/api/<bot>/site", methods=["POST"])
@api
def api_site_set(bot):
    _site_ad(bot).site_set(request.get_json() or {})
    return jsonify(ok=True, **_site_ad(bot).site_get())


@global_bp.route("/global/api/<bot>/site/user", methods=["POST"])
@api
def api_site_user(bot):
    j = request.get_json() or {}
    _site_ad(bot).site_user_action(j.get("login"), str(j.get("action")), j.get("value"))
    return jsonify(ok=True, **_site_ad(bot).site_get())


@global_bp.route("/global/api/<bot>/site/review", methods=["POST"])
@api
def api_site_review(bot):
    j = request.get_json() or {}
    _site_ad(bot).site_review_action(j.get("id"), str(j.get("action")), j.get("review"))
    return jsonify(ok=True, **_site_ad(bot).site_get())


# admin notifications
@global_bp.route("/global/api/<bot>/notifications")
@api
def api_notifications_get(bot):
    ad = need_adapter(bot)
    if not getattr(ad, "supports_notifications", False):
        raise AdapterError("this bot's code does not support notification switches yet")
    return jsonify(ok=True, **ad.notifications())


@global_bp.route("/global/api/<bot>/notifications", methods=["POST"])
@api
def api_notifications_set(bot):
    ad = need_adapter(bot)
    if not getattr(ad, "supports_notifications", False):
        raise AdapterError("this bot's code does not support notification switches yet")
    j = request.get_json() or {}
    ad.set_notifications(str(j.get("admin", "")), [str(x) for x in (j.get("off") or [])])
    return jsonify(ok=True, **ad.notifications())


# currency
@global_bp.route("/global/api/<bot>/currency", methods=["POST"])
@api
def api_currency_set(bot):
    ad = need_adapter(bot)
    if not getattr(ad, "supports_currency", False):
        raise AdapterError("this bot's code does not support currencies yet")
    ok, msg, cur = currency_util.clean_currency((request.get_json() or {}).get("currency"))
    if not ok:
        raise AdapterError(msg)
    ad.set_currency(cur)
    return jsonify(ok=True, currency=cur)


@global_bp.route("/global/api/currency-rate")
@api
def api_currency_rate():
    code = request.args.get("code", "").upper()
    if len(code) != 3 or not code.isalpha():
        raise AdapterError("enter a 3-letter currency code, e.g. GEL")
    rate, src = currency_util.fetch_usd_per_unit(code)
    if rate is None:
        raise AdapterError(src + " - set a fixed rate instead")
    return jsonify(ok=True, usd_per_unit=rate, msg=f"1 {code} = {rate:.4f} USD (live, {src})")


# purchase history
def need_purchases(bot):
    ad = need_adapter(bot)
    if not getattr(ad, "supports_purchases", False):
        raise AdapterError("this bot's code does not support purchase history yet")
    return ad


@global_bp.route("/global/api/<bot>/users/<int:uid>/purchases")
@api
def api_purchases(bot, uid):
    return jsonify(purchases=need_purchases(bot).purchases(uid))


@global_bp.route("/global/api/<bot>/users/<int:uid>/purchases", methods=["POST"])
@api
def api_purchase_add(bot, uid):
    j = request.get_json() or {}
    pid = need_purchases(bot).add_purchase(uid, j.get("product", ""), float(j.get("price") or 0), j.get("note", ""))
    return jsonify(ok=True, id=pid)


@global_bp.route("/global/api/<bot>/purchases/<int:pid>/delete", methods=["POST"])
@api
def api_purchase_delete(bot, pid):
    if not need_purchases(bot).delete_purchase(pid):
        raise AdapterError("purchase not found")
    return jsonify(ok=True)


# products
@global_bp.route("/global/api/<bot>/products")
@api
def api_products(bot):
    ad = need_adapter(bot)
    if not ad.has_products:
        raise AdapterError("this bot has no product catalog")
    return jsonify(products=ad.products(), meta=ad.meta(), kind=ad.kind)


@global_bp.route("/global/api/<bot>/products", methods=["POST"])
@api
def api_product_save(bot):
    ad = need_adapter(bot)
    j = request.get_json() or {}
    owner = j.get("owner")
    if owner is not None:
        if not getattr(ad, "supports_private", False):
            raise AdapterError("this bot's code does not support private listings yet")
        pid = ad.save_product(j.get("id"), j, owner=int(owner))
    else:
        pid = ad.save_product(j.get("id"), j)
    return jsonify(ok=True, id=pid)


# private listings
def need_private(bot):
    ad = need_adapter(bot)
    if not getattr(ad, "supports_private", False):
        raise AdapterError("this bot's code does not support private listings yet "
                           "(restart it after updating, or update its template)")
    return ad


@global_bp.route("/global/api/<bot>/private")
@api
def api_private_list(bot):
    ad = need_private(bot)
    st = status_map(bot)
    names = {u["id"]: u["username"] for u in ad.users()}
    rows = ad.list_private()
    for r in rows:
        r["username"] = names.get(r["user_id"]) or st.get(r["user_id"], {}).get("username") or ""
        r["known"] = r["user_id"] in names
    return jsonify(listings=rows)


@global_bp.route("/global/api/<bot>/private", methods=["POST"])
@api
def api_private_create(bot):
    ad = need_private(bot)
    uid = int((request.get_json() or {})["user_id"])
    if uid <= 0:
        raise ValueError("user_id")
    ad.create_private(uid)
    return jsonify(ok=True)


@global_bp.route("/global/api/<bot>/private/delete", methods=["POST"])
@api
def api_private_delete(bot):
    need_private(bot).delete_private(int((request.get_json() or {})["user_id"]))
    return jsonify(ok=True)


@global_bp.route("/global/api/<bot>/private/<int:uid>/products")
@api
def api_private_products(bot, uid):
    ad = need_private(bot)
    return jsonify(products=ad.private_products(uid), meta=ad.meta(), kind=ad.kind)


@global_bp.route("/global/api/<bot>/products/delete", methods=["POST"])
@api
def api_product_delete(bot):
    return jsonify(ok=need_adapter(bot).delete_product((request.get_json() or {})["id"]))


@global_bp.route("/global/api/<bot>/products/image", methods=["POST"])
@api
def api_product_image_upload(bot):
    """Store a delivery picture inside the bot's own delivery_images/ folder."""
    ad = need_adapter(bot)
    if not getattr(ad, "has_delivery_image", False):
        raise AdapterError("this bot does not support delivery pictures")
    f = request.files.get("photo")
    if not f or not f.filename:
        raise AdapterError("no file")
    ext = Path(f.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise AdapterError("picture must be jpg, png or webp")
    data = f.read()
    if len(data) > MAX_PHOTO_BYTES:
        raise AdapterError("picture too large (max 10 MB)")
    folder = ad.dir / "delivery_images"
    folder.mkdir(exist_ok=True)
    name = uuid.uuid4().hex + ext
    (folder / name).write_bytes(data)
    return jsonify(ok=True, name=name)


@global_bp.route("/global/api/<bot>/products/image/<name>")
@api
def api_product_image_get(bot, name):
    from flask import send_from_directory
    ad = need_adapter(bot)
    return send_from_directory(ad.dir / "delivery_images", Path(name).name)


@global_bp.route("/global/api/<bot>/groups", methods=["POST"])
@api
def api_group_add(bot):
    """Add a district (json bots) or a category (shop bots)."""
    ad = need_adapter(bot)
    j = request.get_json() or {}
    name = str(j.get("name", "")).strip()
    if not name:
        raise AdapterError("name is required")
    if ad.kind == "json":
        return jsonify(ok=True, id=ad.add_district(name, j.get("city") or "tbilisi"))
    return jsonify(ok=True, id=ad.add_category(name, j.get("city") or ""))


@global_bp.route("/global/api/<bot>/categories", methods=["POST"])
@api
def api_category_add(bot):
    """Add a category to a JSON-engine bot (the shop bots use /groups for theirs)."""
    ad = need_adapter(bot)
    if ad.kind != "json" or not getattr(ad, "supports_categories", False):
        raise AdapterError("this bot's code does not support categories yet")
    j = request.get_json() or {}
    name = str(j.get("name", "")).strip()
    if not name:
        raise AdapterError("name is required")
    city = re.sub(r"[^a-z0-9_]", "", str(j.get("city", "")).strip().lower())
    return jsonify(ok=True, id=ad.add_category(name[:60], city))


@global_bp.route("/global/api/<bot>/categories/delete", methods=["POST"])
@api
def api_category_delete(bot):
    ad = need_adapter(bot)
    if ad.kind != "json" or not getattr(ad, "supports_categories", False):
        raise AdapterError("this bot's code does not support categories yet")
    return jsonify(ok=ad.delete_category((request.get_json() or {})["id"]))


@global_bp.route("/global/api/<bot>/groups/delete", methods=["POST"])
@api
def api_group_delete(bot):
    ad = need_adapter(bot)
    gid = (request.get_json() or {})["id"]
    return jsonify(ok=ad.delete_district(gid) if ad.kind == "json" else ad.delete_category(gid))


# announcements
def _save_upload(f) -> str | None:
    if not f or not f.filename:
        return None
    data = f.read()
    if len(data) > MAX_PHOTO_BYTES:
        raise AdapterError("photo too large (max 10 MB)")
    ext = Path(f.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        raise AdapterError("photo must be jpg/png/webp/gif")
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    dest.write_bytes(data)
    return str(dest)


@global_bp.route("/global/api/announce", methods=["POST"])
@api
def api_announce():
    text = (request.form.get("text") or "").strip()
    photo = _save_upload(request.files.get("photo"))
    if not text and not photo:
        raise AdapterError("write a message or attach a photo")
    html = request.form.get("html") == "1"
    bots = [b for b in request.form.get("bots", "").split(",") if b]
    if not bots:
        raise AdapterError("pick at least one bot")
    when = (request.form.get("run_at") or "").strip()      # UTC "YYYY-MM-DD HH:MM:SS"
    every = int(request.form.get("every_min") or 0) or None
    jobs, scheduled = [], []
    for b in bots:
        need_adapter(b)
        if when:
            datetime.strptime(when, TIME_FMT)
            with closing(pdb()) as con, con:
                cur = con.execute(
                    "INSERT INTO schedules (bot, text, photo, html, run_at, every_min, created) VALUES (?,?,?,?,?,?,?)",
                    (b, text, photo, int(html), when, every, now_str()))
                scheduled.append(cur.lastrowid)
        else:
            if job_running(b):
                raise AdapterError(f"{b} already has a job running")
            jobs.append(start_broadcast(b, text, photo, html))
    return jsonify(ok=True, jobs=jobs, scheduled=scheduled)


@global_bp.route("/global/api/schedules")
@api
def api_schedules():
    with closing(pdb()) as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM schedules ORDER BY run_at")]
    names = {k: v.get("display_name", k) for k, v in load_registry().items()}
    for r in rows:
        r["display"] = names.get(r["bot"], r["bot"])
        r["photo"] = bool(r["photo"])
    native = []
    for name in load_registry():
        ad = get_adapter(name) if name not in SKIP_BOTS else None
        if isinstance(ad, JsonAdapter):
            native += [{**a, "bot": name, "display": names.get(name, name)} for a in ad.native_schedules()]
    return jsonify(schedules=rows, native=native)


@global_bp.route("/global/api/schedules/delete", methods=["POST"])
@api
def api_schedule_delete():
    j = request.get_json() or {}
    if j.get("native"):
        return jsonify(ok=need_adapter(j["bot"]).delete_native_schedule(int(j["id"])))
    with closing(pdb()) as con, con:
        con.execute("DELETE FROM schedules WHERE id=?", (int(j["id"]),))
    return jsonify(ok=True)


# scan / clean
@global_bp.route("/global/api/<bot>/scan", methods=["POST"])
@api
def api_scan(bot):
    if job_running(bot):
        raise AdapterError("a job is already running for this bot")
    j = request.get_json() or {}
    return jsonify(ok=True, job=start_scan(bot, bool(j.get("profiles"))))


@global_bp.route("/global/api/<bot>/clean", methods=["POST"])
@api
def api_clean(bot):
    ad = need_adapter(bot)
    if job_running(bot):
        raise AdapterError("a job is running for this bot — wait for it to finish")
    j = request.get_json() or {}
    include_balance = bool(j.get("include_balance"))
    reasons = set(j.get("reasons") or ["blocked", "deleted", "not_found"])
    st = status_map(bot)
    users = {u["id"]: u for u in ad.users()}
    victims, kept = [], []
    for uid, s in st.items():
        if s["status"] != "dead" or uid not in users or s["reason"] not in reasons:
            continue
        (victims if (include_balance or users[uid]["balance"] <= 0) else kept).append(uid)
    if j.get("dry_run"):
        return jsonify(ok=True, would_remove=len(victims), kept_with_balance=len(kept))
    backup = ad.backup() if victims else None
    removed = ad.delete_users(victims) if victims else 0
    with closing(pdb()) as con, con:
        con.executemany("DELETE FROM user_status WHERE bot=? AND uid=?", [(bot, v) for v in victims])
    return jsonify(ok=True, removed=removed, kept_with_balance=len(kept), backup=backup)


@global_bp.route("/global/api/<bot>/forget", methods=["POST"])
@api
def api_forget(bot):
    """Drop stored scan results (e.g. a user unblocked the bot and should be re-checked)."""
    with closing(pdb()) as con, con:
        con.execute("DELETE FROM user_status WHERE bot=?", (bot,))
    return jsonify(ok=True)


@global_bp.route("/global/api/jobs")
@api
def api_jobs():
    with closing(pdb()) as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 30")]
    return jsonify(jobs=rows)


@global_bp.route("/global/api/jobs/<int:jid>/cancel", methods=["POST"])
@api
def api_job_cancel(jid):
    with closing(pdb()) as con, con:
        con.execute("UPDATE jobs SET status='cancelling' WHERE id=? AND status='running'", (jid,))
    return jsonify(ok=True)
