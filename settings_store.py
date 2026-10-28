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
