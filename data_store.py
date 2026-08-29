"""
Simple JSON-based data store for dynamic products, districts, users,
and scheduled announcements.

File: data.json (auto-created next to this module)
"""

import json
import os
from datetime import datetime
from typing import Any

DATA_PATH = os.path.join(os.path.dirname(__file__), "data.json")

_DEFAULT: dict[str, Any] = {
    "products": {
        # "product:apocalypse_1000": {
        #     "name": "Apocalypse (1000)",
        #     "price_usd": 20000,
        #     "description": "+AAA",
        #     "quantity": -1,        # -1 = unlimited
        #     "city": "tbilisi",
        # },
    },
    "districts": {
        # "district:moskovis": {
        #     "name": "Moskovis Gamziri",
        #     "city": "tbilisi",
        #     "image": "moskovisgamz.jpg",
        # },
    },
    "users": {
        # "123456": {"username": "alice", "joined": "2025-01-01", "banned": false}
    },
    "announcements_scheduled": [
        # {"id": 1, "text": "...", "photo": null, "run_at": "...", "recurring": null, "sent": false}
    ],
    "stats": {
        "total_orders": 0,
        "total_revenue_usd": 0.0,
    },
}


def _load() -> dict:
    if not os.path.exists(DATA_PATH):
        _save(_DEFAULT)
        return _DEFAULT.copy()
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Products ───────────────────────────────────────────────────────────

def get_products(city: str = "tbilisi") -> dict[str, dict]:
    data = _load()
    return {k: v for k, v in data["products"].items() if v.get("city") == city}


def add_product(slug: str, name: str, price_usd: float, description: str,
                quantity: int = -1, city: str = "tbilisi",
                districts: list[str] | None = None) -> None:
    data = _load()
    data["products"][slug] = {
        "name": name,
        "price_usd": price_usd,
        "description": description,
        "quantity": quantity,
        "city": city,
        "districts": districts or [],  # list of district slugs assigned to this product
    }
    _save(data)


def delete_product(slug: str) -> bool:
    data = _load()
    if slug in data["products"]:
        del data["products"][slug]
        _save(data)
        return True
    return False


def get_product(slug: str) -> dict | None:
    data = _load()
    return data["products"].get(slug)


def update_product(slug: str, **fields) -> bool:
    data = _load()
    if slug not in data["products"]:
        return False
    data["products"][slug].update(fields)
    _save(data)
    return True


def get_product_districts(slug: str) -> list[str]:
    """Return the list of district slugs assigned to a product."""
    product = get_product(slug)
    if not product:
        return []
    return product.get("districts", [])


def toggle_product_district(slug: str, district_slug: str) -> bool:
    """Toggle a district on/off for a product. Returns new state (True=assigned)."""
    data = _load()
    if slug not in data["products"]:
        return False
    districts = data["products"][slug].get("districts", [])
    if district_slug in districts:
        districts.remove(district_slug)
        assigned = False
    else:
        districts.append(district_slug)
        assigned = True
    data["products"][slug]["districts"] = districts
    _save(data)
    return assigned


# ── Districts ──────────────────────────────────────────────────────────

def get_districts(city: str = "tbilisi") -> dict[str, dict]:
    data = _load()
    return {k: v for k, v in data["districts"].items() if v.get("city") == city}


def add_district(slug: str, name: str, city: str = "tbilisi",
                 image: str = "") -> None:
    data = _load()
    data["districts"][slug] = {"name": name, "city": city, "image": image}
    _save(data)


def delete_district(slug: str) -> bool:
    data = _load()
    if slug in data["districts"]:
        del data["districts"][slug]
        _save(data)
        return True
    return False


# ── Users ──────────────────────────────────────────────────────────────

def register_user(user_id: int, username: str = "") -> bool:
    """Register user. Returns True if this is a NEW user (first time)."""
    data = _load()
    uid = str(user_id)
    if uid not in data["users"]:
        data["users"][uid] = {
            "username": username,
            "joined": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "banned": False,
            "lang": "ka",
            "balance": 0.0,
        }
        _save(data)
        return True
    return False


def get_user_lang(user_id: int) -> str:
    """Get user's chosen language code (ka, ru, en). Defaults to 'ka'."""
    u = get_user(user_id)
    if u:
        return u.get("lang", "ka")
    return "ka"


def set_user_lang(user_id: int, lang: str) -> None:
    """Set user's chosen language code."""
    data = _load()
    uid = str(user_id)
    if uid in data["users"]:
        data["users"][uid]["lang"] = lang
        _save(data)


def get_all_user_ids() -> list[int]:
    data = _load()
    return [int(uid) for uid in data["users"]]


def get_user(user_id: int) -> dict | None:
    data = _load()
    return data["users"].get(str(user_id))


def set_user_banned(user_id: int, banned: bool) -> bool:
    data = _load()
    uid = str(user_id)
    if uid in data["users"]:
        data["users"][uid]["banned"] = banned
        _save(data)
        return True
    return False


def is_user_banned(user_id: int) -> bool:
    u = get_user(user_id)
    return u.get("banned", False) if u else False


def get_user_balance(user_id: int) -> float:
    """Get user's current balance. Defaults to 0.0."""
    u = get_user(user_id)
    if u:
        return float(u.get("balance", 0.0))
    return 0.0


def set_user_balance(user_id: int, amount: float) -> bool:
    """Set user's balance. Returns True if user exists."""
    data = _load()
    uid = str(user_id)
    if uid in data["users"]:
        data["users"][uid]["balance"] = amount
        _save(data)
        return True
    return False


def user_count() -> int:
    data = _load()
    return len(data["users"])


# ── Scheduled announcements ───────────────────────────────────────────

def add_scheduled_announcement(text: str, run_at: str,
                                recurring: str | None = None,
                                photo_path: str | None = None) -> int:
    """Add a scheduled announcement. Returns its id."""
    data = _load()
    new_id = max((a["id"] for a in data["announcements_scheduled"]), default=0) + 1
    data["announcements_scheduled"].append({
        "id": new_id,
        "text": text,
        "photo": photo_path,
        "run_at": run_at,
        "recurring": recurring,  # None | "daily" | "weekly" | "monthly"
        "sent": False,
    })
    _save(data)
    return new_id


def get_pending_announcements() -> list[dict]:
    data = _load()
    return [a for a in data["announcements_scheduled"] if not a["sent"]]


def mark_announcement_sent(ann_id: int) -> None:
    data = _load()
    for a in data["announcements_scheduled"]:
        if a["id"] == ann_id:
            a["sent"] = True
            break
    _save(data)


def delete_scheduled_announcement(ann_id: int) -> bool:
    data = _load()
    before = len(data["announcements_scheduled"])
    data["announcements_scheduled"] = [
        a for a in data["announcements_scheduled"] if a["id"] != ann_id
    ]
    if len(data["announcements_scheduled"]) < before:
        _save(data)
        return True
    return False


# ── Stats ──────────────────────────────────────────────────────────────

def get_stats() -> dict:
    data = _load()
    stats = data.get("stats", {})
    stats["user_count"] = len(data["users"])
    stats["product_count"] = len(data["products"])
    stats["district_count"] = len(data["districts"])
    stats["scheduled_announcements"] = len(
        [a for a in data["announcements_scheduled"] if not a["sent"]]
    )
    return stats


def increment_order(amount_usd: float) -> None:
    data = _load()
    data["stats"]["total_orders"] = data["stats"].get("total_orders", 0) + 1
    data["stats"]["total_revenue_usd"] = data["stats"].get("total_revenue_usd", 0) + amount_usd
    _save(data)


# ── Migration: seed from hardcoded texts.py data ──────────────────────

def seed_from_texts() -> None:
    """
    If data.json has no products/districts, populate from texts.py constants
    so the bot keeps working with existing data after the admin panel is added.
    """
    import re as _re
    data = _load()
    changed = False

    if not data["districts"]:
        import texts as _t
        for label, cb in _t.TBILISI_DISTRICTS.items():
            data["districts"][cb] = {
                "name": label,
                "city": "tbilisi",
                "image": "",
            }
        changed = True

    # Migration: set district images for known districts if missing
    _DISTRICT_IMAGES = {
        "district:moskovis": "moskovisgamz.jpg",
        "district:lilo": "lilo.jpg",
        "district:didi_digomi": "dididigomi.jpg",
        "district:varketili": "varketili.jpg",
        "district:avchala": "avchala.jpg",
    }
    for d_slug, d_info in data.get("districts", {}).items():
        if not d_info.get("image") and d_slug in _DISTRICT_IMAGES:
            d_info["image"] = _DISTRICT_IMAGES[d_slug]
            changed = True

    if not data["products"]:
        import texts as _t
        # Collect all district slugs so we can assign them to each product
        all_district_slugs = list(data["districts"].keys())
        for label, cb in _t.TBILISI_PRODUCTS.items():
            m = _re.search(r"(\d+(?:\.\d+)?)\s*\$", label)
            price = float(m.group(1)) if m else 0
            data["products"][cb] = {
                "name": label,
                "price_usd": price,
                "description": "+AAA",
                "quantity": -1,
                "city": "tbilisi",
                "districts": all_district_slugs.copy(),
            }
        changed = True

    # Migration: add "districts" field to any product that lacks it
    for slug, prod in data["products"].items():
        if "districts" not in prod:
            prod["districts"] = list(data["districts"].keys())
            changed = True

    if changed:
        _save(data)
