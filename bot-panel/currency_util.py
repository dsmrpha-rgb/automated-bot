"""Shared currency helpers for the Constructor and the Global Admin."""
from __future__ import annotations

import json
import re
import urllib.request

PRESETS = [
    ("USD", "$"), ("EUR", "€"), ("GEL", "ლ"), ("RUB", "₽"), ("UAH", "₴"), ("TRY", "₺"),
    ("GBP", "£"), ("PLN", "zł"), ("KZT", "₸"), ("AED", "AED"), ("CAD", "C$"), ("AUD", "A$"),
]
_CODE = re.compile(r"^[A-Z]{3}$")


def clean_currency(cur) -> tuple[bool, str, dict | None]:
    """Validate an incoming currency object. (ok, error, clean) — clean None means 'no currency'."""
    if cur in (None, {}, False) or (isinstance(cur, dict) and not cur.get("code")):
        return True, "", None
    if not isinstance(cur, dict):
        return False, "currency must be an object", None
    code = str(cur.get("code", "")).strip().upper()
    if not _CODE.match(code):
        return False, "currency code must be 3 letters, e.g. GEL", None
    symbol = str(cur.get("symbol", "") or "").strip()
    if not symbol or len(symbol) > 6:
        return False, "currency symbol must be 1-6 characters, e.g. ლ", None
    rate = cur.get("rate", "auto")
    if rate != "auto":
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            return False, "exchange rate must be a number", None
        if not (rate > 0) or rate > 1_000_000:
            return False, "exchange rate must be greater than 0", None
    return True, "", {"code": code, "symbol": symbol, "position": "before" if cur.get("position") == "before" else "after",
                      "space": bool(cur.get("space")), "rate": rate}


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def fetch_usd_per_unit(code: str):
    """(usd_per_unit, source) for a currency code, or (None, reason)."""
    code = code.upper()
    if code == "USD":
        return 1.0, "fixed"
    d = _get(f"https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies={code.lower()}")
    per_usd = ((d or {}).get("tether") or {}).get(code.lower())
    src = "CoinGecko"
    if not per_usd:
        d = _get("https://open.er-api.com/v6/latest/USD")
        per_usd = ((d or {}).get("rates") or {}).get(code)
        src = "open.er-api.com"
    if per_usd and float(per_usd) > 0:
        return 1.0 / float(per_usd), src
    return None, "no live rate found for " + code
