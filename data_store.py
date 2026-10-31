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

def _same_city(a, b) -> bool:
    """Cities are compared ignoring case ('Tbilisi' == 'tbilisi')."""
    return str(a or "tbilisi").strip().lower() == str(b or "tbilisi").strip().lower()


def get_products(city: str = "tbilisi", user_id: int | None = None, all_products: bool = False) -> dict[str, dict]:
    """Product list.

    Default / city only: the GLOBAL listing (private products excluded).
    user_id given: that user's private listing if they have one, else the global listing.
    all_products=True: every product of the city incl. private ones (for lookups by slug).
    """
    data = _load()
    prods = data["products"]
    if all_products:
        return {k: v for k, v in prods.items() if _same_city(v.get("city"), city)}
    if user_id is not None and str(user_id) in data.get("private_listings", {}):
        return {k: v for k, v in prods.items() if v.get("owner") == user_id}
    return {k: v for k, v in prods.items() if _same_city(v.get("city"), city) and v.get("owner") is None}


# ── Private listings ───────────────────────────────────────────────────

def has_private_listing(user_id: int) -> bool:
    return str(user_id) in _load().get("private_listings", {})


def create_private_listing(user_id: int) -> None:
    data = _load()
    data.setdefault("private_listings", {}).setdefault(
        str(user_id), {"created": datetime.now().strftime("%Y-%m-%d %H:%M")})
    _save(data)


def delete_private_listing(user_id: int) -> None:
    data = _load()
    data.get("private_listings", {}).pop(str(user_id), None)
    data["products"] = {k: v for k, v in data["products"].items() if v.get("owner") != user_id}
    _save(data)


def list_private_listings() -> list[dict]:
    data = _load()
    return [{"user_id": int(uid), "products": sum(1 for p in data["products"].values() if p.get("owner") == int(uid))}
            for uid in data.get("private_listings", {})]


def get_private_products(user_id: int) -> dict[str, dict]:
    return {k: v for k, v in _load()["products"].items() if v.get("owner") == user_id}


def set_product_owner(slug: str, user_id: int) -> None:
    data = _load()
    if slug in data["products"]:
        data["products"][slug]["owner"] = user_id
        _save(data)


def product_allowed(user_id: int, slug: str) -> bool:
    """Private products are visible only to their owner; users with a private listing see only theirs."""
    data = _load()
    p = data["products"].get(slug)
    if p is None:
        return False
    owner = p.get("owner")
    if owner is not None:
        return owner == user_id
    return str(user_id) not in data.get("private_listings", {})


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


VARIANT_KEYS = ("land", "building")


def product_variants(product: dict) -> list[str]:
    """Variants a product is sold as: 'land' (buried in a district) / 'building'. Unset = both."""
    v = product.get("variants")
    if not isinstance(v, list):
        return list(VARIANT_KEYS)
    return [k for k in VARIANT_KEYS if k in v]


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
    return {k: v for k, v in data["districts"].items() if _same_city(v.get("city"), city)}


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


# ── Categories (optional layer between city and product) ───────────────

def get_categories(city: str | None = None) -> dict[str, dict]:
    """Categories; with a city: those for that city plus the ones shared by every city (city '')."""
    cats = _load().get("categories", {})
    if city is None:
        return dict(cats)
    return {k: v for k, v in cats.items() if not v.get("city") or _same_city(v.get("city"), city)}


def add_category(slug: str, name: str, city: str = "", image: str = "") -> None:
    data = _load()
    data.setdefault("categories", {})[slug] = {"name": name, "city": city, "image": image}
    _save(data)


def delete_category(slug: str) -> bool:
    """Delete a category; its products simply become uncategorised."""
    data = _load()
    if slug not in data.get("categories", {}):
        return False
    del data["categories"][slug]
    for p in data.get("products", {}).values():
        if p.get("category") == slug:
            p["category"] = ""
    _save(data)
    return True


def get_cities() -> list[str]:
    """Every city slug used by products, districts or categories."""
    data = _load()
    found = {str(p.get("city") or "tbilisi").lower() for p in data.get("products", {}).values() if p.get("owner") is None}
    found |= {str(d.get("city") or "tbilisi").lower() for d in data.get("districts", {}).values()}
    found |= {str(c["city"]).lower() for c in data.get("categories", {}).values() if c.get("city")}
    return sorted(found)


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


# ── Referrals ──────────────────────────────────────────────────────────

def set_referrer(user_id: int, referrer_id: int) -> bool:
    """Remember who invited a (new) user. Ignored for self-invites, unknown inviters or already-invited users."""
    if int(user_id) == int(referrer_id):
        return False
    data = _load()
    u, r = data["users"].get(str(user_id)), data["users"].get(str(referrer_id))
    if u is None or r is None or u.get("referrer"):
        return False
    u["referrer"] = int(referrer_id)
    _save(data)
    return True


def count_referrals(user_id: int) -> int:
    return sum(1 for u in _load()["users"].values() if u.get("referrer") == int(user_id))


def get_ref_earned(user_id: int) -> float:
    u = get_user(user_id)
    return round(float(u.get("ref_earned", 0.0)), 2) if u else 0.0


def apply_referral_bonus(user_id: int, price: float, percent: float) -> tuple[int, float] | None:
    """Pay the inviter `percent` % of a purchase into their balance. Returns (inviter_id, bonus) or None."""
    if percent <= 0 or price <= 0:
        return None
    data = _load()
    u = data["users"].get(str(user_id))
    ref = u.get("referrer") if u else None
    r = data["users"].get(str(ref)) if ref else None
    if r is None:
        return None
    bonus = round(float(price) * float(percent) / 100.0, 2)
    if bonus <= 0:
        return None
    r["balance"] = round(float(r.get("balance", 0.0)) + bonus, 2)
    r["ref_earned"] = round(float(r.get("ref_earned", 0.0)) + bonus, 2)
    _save(data)
    return int(ref), bonus


def give_demo_balance(user_id: int, amount: float) -> bool:
    """One-time demo balance: only if the user was never given one and has no money yet."""
    data = _load()
    u = data["users"].get(str(user_id))
    if u is None or u.get("demo_balance"):
        return False
    u["demo_balance"] = True              # flagged either way, so it is decided exactly once
    if float(u.get("balance", 0.0)) == 0.0:
        u["balance"] = round(float(amount), 2)
        _save(data)
        return True
    _save(data)
    return False


def deduct_balance(user_id: int, amount: float) -> bool:
    """Take `amount` from the balance if there is enough (all-or-nothing). True when charged."""
    data = _load()
    u = data["users"].get(str(user_id))
    if u is None or float(u.get("balance", 0.0)) + 1e-9 < float(amount):
        return False
    u["balance"] = round(float(u.get("balance", 0.0)) - float(amount), 2)
    _save(data)
    return True


def take_stock(slug: str) -> bool:
    """Reduce a product's stock by one. False when it is out of stock (quantity 0); -1 = unlimited."""
    data = _load()
    p = data["products"].get(slug)
    if p is None:
        return False
    qty = p.get("quantity", -1)
    if qty == 0:
        return False
    if qty > 0:
        p["quantity"] = qty - 1
        _save(data)
    return True


def user_count() -> int:
    data = _load()
    return len(data["users"])


def get_counts() -> dict:
    """Live numbers usable in button labels: {cities} {products_count}."""
    data = _load()
    prods = [p for p in data.get("products", {}).values() if p.get("owner") is None]
    cities = {str(p.get("city") or "tbilisi").lower() for p in prods}
    cities |= {str(d.get("city") or "tbilisi").lower() for d in data.get("districts", {}).values()}
    return {"cities": len(cities), "products_count": len(prods)}


# ── Purchase history (recorded by admins) ─────────────────────────────

def add_purchase(user_id: int, product: str, price_usd: float = 0.0,
                 note: str = "", by: str = "bot") -> int:
    """Record a purchase for a user. Returns the new purchase id."""
    data = _load()
    items = data.setdefault("purchases", [])
    pid = max([p.get("id", 0) for p in items] + [0]) + 1
    items.append({
        "id": pid, "user_id": int(user_id), "product": str(product)[:200],
        "price_usd": float(price_usd or 0), "note": str(note or "")[:300],
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "by": by,
    })
    _save(data)
    return pid


def get_user_purchases(user_id: int, limit: int | None = None) -> list[dict]:
    """A user's purchases, newest first."""
    items = [p for p in _load().get("purchases", []) if p.get("user_id") == int(user_id)]
    items.sort(key=lambda p: p.get("id", 0), reverse=True)
    return items[:limit] if limit else items


def get_demo_purchases(user_id: int) -> list[str] | None:
    """Demo purchase links assigned to a user (None = not assigned yet)."""
    u = get_user(user_id)
    val = u.get("demo_purchases") if u else None
    return list(val) if isinstance(val, list) else None


def set_demo_purchases(user_id: int, links: list[str]) -> None:
    data = _load()
    u = data["users"].get(str(user_id))
    if u is not None:
        u["demo_purchases"] = list(links)
        _save(data)


def delete_purchase(purchase_id: int) -> bool:
    data = _load()
    items = data.get("purchases", [])
    kept = [p for p in items if p.get("id") != int(purchase_id)]
    if len(kept) == len(items):
        return False
    data["purchases"] = kept
    _save(data)
    return True


# ── Channel gate (users who passed the "join our channel" check) ───────

def is_gate_ok(user_id: int) -> bool:
    u = get_user(user_id)
    return bool(u and u.get("gate_ok"))


def set_gate_ok(user_id: int, ok: bool) -> None:
    data = _load()
    u = data["users"].get(str(user_id))
    if u is not None:
        u["gate_ok"] = bool(ok)
        _save(data)


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
