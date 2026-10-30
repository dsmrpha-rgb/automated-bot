"""Live settings read from menu_config.json (written by the Bot Panel constructor).

Cached by file mtime, so a Publish in the constructor takes effect immediately
without restarting the bot. Every helper falls back to the built-in default
when the config has no such section, so older bots keep working unchanged.
"""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "menu_config.json")

_cache: dict = {"mtime": None, "cfg": {}}

DEFAULT_COINS = [
    {"code": "BTC", "label": "Bitcoin", "cg_id": "bitcoin", "stable": False},
    {"code": "LTC", "label": "Litecoin", "cg_id": "litecoin", "stable": False},
    {"code": "USDT", "label": "USDT TRC20", "cg_id": "", "stable": True},
]


def cfg() -> dict:
    try:
        m = os.path.getmtime(CONFIG_PATH)
    except OSError:
        return {}
    if _cache["mtime"] != m:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _cache["cfg"] = json.load(f)
        except Exception:
            _cache["cfg"] = {}
        _cache["mtime"] = m
    return _cache["cfg"]


def string_override(key: str, lang: str) -> str | None:
    """Text replacing a built-in system message (strings: {KEY: {lang: text}})."""
    val = (cfg().get("strings") or {}).get(key)
    if isinstance(val, dict):
        text = val.get(lang)
        if text is None:
            text = val.get(cfg().get("default_lang", "ka"))
        if isinstance(text, str) and text != "":
            return text
    return None


def admin_hidden() -> set[str]:
    """Admin menu items switched off in the constructor (admin_menu.hidden)."""
    hidden = (cfg().get("admin_menu") or {}).get("hidden") or []
    return {str(h) for h in hidden}


def referral() -> dict | None:
    """Referral programme switch (referral: {enabled, percent}) - None when off."""
    r = cfg().get("referral")
    if not isinstance(r, dict) or not r.get("enabled"):
        return None
    try:
        pct = float(r.get("percent", 2))
    except (TypeError, ValueError):
        pct = 2.0
    return {"percent": max(0.0, min(pct, 100.0))}


def demo_balance() -> dict | None:
    """Demo balance switch (demo_balance: {enabled, min, max}) - None when off."""
    d = cfg().get("demo_balance")
    if not isinstance(d, dict) or not d.get("enabled"):
        return None
    try:
        lo, hi = float(d.get("min", 3)), float(d.get("max", 14))
    except (TypeError, ValueError):
        lo, hi = 3.0, 14.0
    return {"min": min(lo, hi), "max": max(lo, hi)}


def coins() -> list[dict]:
    """Payment coins offered to users (payments.coins), defaults BTC/LTC/USDT."""
    custom = (cfg().get("payments") or {}).get("coins")
    if not isinstance(custom, list) or not custom:
        return [dict(c) for c in DEFAULT_COINS]
    out = []
    for c in custom:
        if not isinstance(c, dict) or not c.get("code") or c.get("enabled") is False:
            continue
        out.append({"code": str(c["code"]).upper(), "label": str(c.get("label") or c["code"]),
                    "cg_id": str(c.get("cg_id") or ""), "stable": bool(c.get("stable"))})
    return out


# ── currency ─────────────────────────────────────────────────────────────────
# Prices, balances and top-up amounts are all stored in the bot's currency; only the
# crypto payment amount is converted (currency -> USD -> coin).
CURRENCY_PRESETS = [
    ("USD", "$"), ("EUR", "€"), ("GEL", "ლ"), ("RUB", "₽"), ("UAH", "₴"), ("TRY", "₺"),
    ("GBP", "£"), ("PLN", "zł"), ("KZT", "₸"), ("AED", "AED"), ("CAD", "C$"), ("AUD", "A$"),
]

_live_rate: dict = {"code": None, "usd_per_unit": None}   # filled by crypto_rates.refresh_fiat_rate()


def currency() -> dict | None:
    """The bot's currency (None = not configured: numbers stay plain, USD is assumed)."""
    c = cfg().get("currency")
    if not isinstance(c, dict) or not c.get("code"):
        return None
    rate = c.get("rate")
    return {
        "code": str(c["code"]).upper(),
        "symbol": str(c.get("symbol") or c["code"]),
        "position": "before" if c.get("position") == "before" else "after",
        "space": bool(c.get("space")),
        "rate": rate if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > 0 else "auto",
    }


def format_amount(value, cur: dict | None = None) -> str:
    """25 -> '25ლ', 25.85 -> '25.85ლ' (at most 2 decimals, no trailing zeros)."""
    cur = cur or currency() or {"symbol": "$", "position": "after", "space": False}
    try:
        num = f"{round(float(value), 2):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)
    if num in ("-0", ""):
        num = "0"
    gap = " " if cur.get("space") else ""
    return f"{cur['symbol']}{gap}{num}" if cur.get("position") == "before" else f"{num}{gap}{cur['symbol']}"


def fmt_money(value) -> str:
    """Money for admin messages: the bot's currency, or dollars when none is set."""
    return format_amount(value)


def money_symbol() -> str:
    return (currency() or {"symbol": "$"})["symbol"]


def usd_per_unit() -> float:
    """How many USD one unit of the bot's currency is worth (1.0 when no currency is set)."""
    c = currency()
    if not c or c["code"] == "USD":
        return 1.0
    if c["rate"] != "auto":
        return float(c["rate"])
    if _live_rate["code"] == c["code"] and _live_rate["usd_per_unit"]:
        return float(_live_rate["usd_per_unit"])
    return 1.0


def set_currency(code: str, symbol: str, position: str = "after", space: bool = False, rate="auto") -> None:
    """Write the currency into menu_config.json (used by the in-bot admin menu)."""
    data = {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass
    data["currency"] = {"code": code.upper(), "symbol": symbol, "position": position, "space": bool(space), "rate": rate}
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    _cache["mtime"] = None


def num(value):
    """A money number for built-in texts: '25' / '25.85' once a currency is set, else unchanged."""
    if currency() is None:
        return value
    try:
        out = f"{round(float(value), 2):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return value
    return "0" if out in ("-0", "") else out


def localize_money_text(text: str) -> str:
    """Built-in texts say '{balance} $': swap the $ for the bot's currency symbol/position."""
    cur = currency()
    if not cur or not isinstance(text, str) or "$" not in text:
        return text
    import re
    gap = " " if cur["space"] else ""

    def sub(m):
        ph = "{" + (m.group(1) or m.group(2)) + "}"
        return f"{cur['symbol']}{gap}{ph}" if cur["position"] == "before" else f"{ph}{gap}{cur['symbol']}"
    return re.sub(r"\{(balance|earned|price|amount)\}\s*\$|\$\s*\{(balance|earned|price|amount)\}", sub, text)
