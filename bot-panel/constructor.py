"""
Bot-Shop Constructor — a Flask blueprint that plugs into the existing Bot Panel.

Lets you build & edit a config-driven Telegram shop bot visually:
  • menu tree: pages, buttons (add one-by-one, reorder, wire to page/builtin/url)
  • per-page text (multi-language) + image
  • .env: multiple bot tokens, admin IDs, crypto wallets
  • save writes menu_config.json into the bot's directory; restart applies it.

Self-contained: reads the same /etc/bot-panel/bots.json the panel uses, guards
with the panel's session login, and never imports app.py (no circular import).
Register it in app.py with:

    from constructor import constructor_bp
    app.register_blueprint(constructor_bp)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, render_template, request, jsonify, redirect, url_for, session, flash
)
from werkzeug.utils import secure_filename

import currency_util

constructor_bp = Blueprint("constructor", __name__)

PANEL_CONFIG = Path(os.environ.get("PANEL_CONFIG", "/etc/bot-panel/bots.json"))
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"} | VIDEO_EXTS   # any media a message can carry
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 48 * 1024 * 1024   # Telegram bots can upload up to 50 MB


def _media_kind(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return "animation" if ext == ".gif" else "video" if ext in VIDEO_EXTS else "photo"
MAX_CONFIG_BYTES = 2 * 1024 * 1024

_SLUG_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_ACTION_RE = re.compile(
    r"^("
    r"page:[a-z0-9_]{1,40}"
    r"|builtin:[a-z0-9_]{1,30}"
    r"|url:https?://\S{1,300}"
    r"|choose:[a-zA-Z0-9_]{1,30}:(?:@|[a-zA-Z0-9_\-.]{1,40}):[a-z0-9_]{1,40}"
    r"|confirm:[a-z0-9_]{1,40}"
    r"|buy:[a-z0-9_]{1,40}(?::[a-z0-9_]{1,40})?"
    r"|setlang:[a-z]{2,3}(?::[a-z0-9_]{1,40})?"
    r"|admin"
    r"|noop"
    r")$"
)
_PAGE_TYPES = {"message", "input", "receipt", "list", "history"}
_STYLES = {"green", "red", "check", "cross"}
_SOURCES = {"products", "districts", "categories", "cities"}


def _action_target_page(action: str) -> str | None:
    """Return the page id an action navigates to, if any."""
    if action.startswith("page:"):
        return action.split(":", 1)[1]
    if action.startswith("confirm:"):
        return action.split(":", 1)[1]
    if action.startswith("buy:"):
        return action.split(":")[1]
    if action.startswith("setlang:"):
        parts = action.split(":")
        return parts[2] if len(parts) > 2 else None
    if action.startswith("choose:"):
        parts = action.split(":", 3)
        return parts[3] if len(parts) == 4 else None
    return None


# ── auth (mirror the panel's login_required) ───────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def _load_bots() -> dict:
    if PANEL_CONFIG.exists():
        return json.loads(PANEL_CONFIG.read_text())
    return {"bots": {}}


def _bot_or_none(name: str) -> dict | None:
    return _load_bots().get("bots", {}).get(name)


def _bot_dir(bot: dict) -> Path:
    return Path(bot["dir"])


def _config_path(bot: dict) -> Path:
    return _bot_dir(bot) / "menu_config.json"


# ── default menu when a bot has no config yet ──────────────────────────────
def _default_config() -> dict:
    return {
        "version": 1,
        "start_page": "main",
        "default_lang": "ka",
        "langs": ["ka", "ru", "en"],
        "pages": {
            "main": {
                "image": "",
                "parse_mode": "HTML",
                "show_admin_button": True,
                "text": {"ka": "მთავარი მენიუ", "ru": "Главное меню", "en": "Main menu"},
                "rows": [],
            }
        },
    }


_ADMIN_MENU_KEYS = {"products", "districts", "broadcast", "dm", "scheduled", "private", "users", "purchases", "stats"}
_VAR_RE = re.compile(r"[^a-zA-Z0-9_]")
_CODE_RE = re.compile(r"^[A-Z0-9]{2,10}$")


def _ml(val, default_lang: str) -> dict:
    """Normalize a (possibly multi-language) text field to {lang: str}."""
    if isinstance(val, str):
        return {default_lang: val}
    if isinstance(val, dict):
        return {str(k): str(v) for k, v in val.items()}
    return {default_lang: ""}


def _clean_hook(hook: dict, default_lang: str):
    """Validate a page on_enter hook; None = invalid, {} = empty."""
    out: dict = {}
    note = _ml(hook.get("notify_admins", ""), default_lang)
    if any(v.strip() for v in note.values()):
        out["notify_admins"] = note
    sv = hook.get("set_vars")
    if isinstance(sv, dict):
        clean = {}
        for k, v in list(sv.items())[:20]:
            k = _VAR_RE.sub("", str(k))[:30]
            if k:
                clean[k] = str(v)[:200]
        if clean:
            out["set_vars"] = clean
    sends = []
    for extra in (hook.get("send") or [])[:10]:
        if not isinstance(extra, dict):
            continue
        img = str(extra.get("image", "") or "")
        if img and (os.path.sep in img or ".." in img or img.startswith("/")):
            return None
        text = _ml(extra.get("text", ""), default_lang)
        if img or any(v.strip() for v in text.values()):
            sends.append({"text": text, "image": img})
    if sends:
        out["send"] = sends
    return out


_RM_MAX_ROWS, _RM_MAX_PER_ROW = 8, 3


def _clean_reply_menu(rm: dict, langs: list, default_lang: str, page_ids: set):
    """Static bottom menu: {enabled, placeholder, intro, rows: [[{label, target}]]}. Returns (ok, message, cleaned).

    The rules are enforced only while it is switched on, so a half-built menu can be saved switched off.
    """
    enabled = bool(rm.get("enabled"))

    def texts(val, limit):
        return {str(k): str(v).strip()[:limit] for k, v in _ml(val, default_lang).items() if str(v).strip()}

    raw_rows = rm.get("rows") if isinstance(rm.get("rows"), list) else []
    if len(raw_rows) > _RM_MAX_ROWS:
        return False, f"bottom menu: at most {_RM_MAX_ROWS} rows", {}
    rows = []
    for row in raw_rows:
        if not isinstance(row, list):
            continue
        if len(row) > _RM_MAX_PER_ROW:
            return False, f"bottom menu: at most {_RM_MAX_PER_ROW} buttons per row", {}
        built = []
        for b in row:
            if not isinstance(b, dict):
                continue
            label = texts(b.get("label", ""), 64)
            target = str(b.get("target", "") or "")
            if not label.get(default_lang):
                if enabled:
                    return False, "bottom menu: every button needs a label in the default language", {}
                continue
            for l in langs:
                label.setdefault(l, label[default_lang])          # missing translations fall back to the default
            if any(v.startswith("/") for v in label.values()):
                return False, "bottom menu: a button label can't start with “/”", {}
            if target not in page_ids:
                if enabled:
                    return False, f"bottom menu: “{label[default_lang]}” opens a screen that doesn't exist", {}
                continue
            built.append({"label": label, "target": target})
        if built:
            rows.append(built)
    if enabled:
        if not rows:
            return False, "bottom menu: add at least one button (or switch it off)", {}
        owner: dict = {}
        for ri, row in enumerate(rows):
            for bi, b in enumerate(row):
                for text in set(b["label"].values()):
                    if owner.setdefault(text, (ri, bi)) != (ri, bi):
                        return False, f"bottom menu: two buttons use the label “{text}”", {}
    out = {"enabled": enabled, "rows": rows}
    placeholder, intro = texts(rm.get("placeholder", ""), 64), texts(rm.get("intro", ""), 200)
    if placeholder:
        out["placeholder"] = placeholder
    if intro:
        out["intro"] = intro
    return True, "", out


def _clean_extras(cfg: dict, default_lang: str, page_ids: set):
    """Top-level sections beyond pages: strings, payments, admin_menu, _layout."""
    out: dict = {}
    strings = cfg.get("strings")
    if isinstance(strings, dict):
        clean = {}
        for key, per_lang in strings.items():
            if not re.match(r"^[A-Z0-9_]{1,60}$", str(key)) or not isinstance(per_lang, dict):
                continue
            vals = {str(l): str(v) for l, v in per_lang.items() if isinstance(v, str) and v != ""}
            if vals:
                clean[str(key)] = vals
        if clean:
            out["strings"] = clean

    pay = cfg.get("payments")
    if isinstance(pay, dict) and isinstance(pay.get("coins"), list):
        coins, seen = [], set()
        for c in pay["coins"][:12]:
            if not isinstance(c, dict):
                continue
            code = str(c.get("code", "")).strip().upper()
            if not _CODE_RE.match(code):
                return False, f"invalid coin code '{code}' (2-10 letters/digits)", {}
            if code in seen:
                return False, f"coin '{code}' listed twice", {}
            seen.add(code)
            coins.append({
                "code": code,
                "label": str(c.get("label") or code)[:30],
                "cg_id": re.sub(r"[^a-z0-9\-]", "", str(c.get("cg_id", "")).lower())[:60],
                "stable": bool(c.get("stable")),
                "enabled": c.get("enabled") is not False,
            })
        if coins:
            out["payments"] = {"coins": coins}

    am = cfg.get("admin_menu")
    if isinstance(am, dict):
        hidden = sorted({str(h) for h in (am.get("hidden") or []) if str(h) in _ADMIN_MENU_KEYS})
        if hidden:
            out["admin_menu"] = {"hidden": hidden}

    gate = cfg.get("gate")
    if isinstance(gate, dict):
        enabled = bool(gate.get("enabled"))
        mode = "dummy" if gate.get("mode") == "dummy" else "verify"
        channel = str(gate.get("channel", "") or "").strip()
        url = str(gate.get("url", "") or "").strip()
        img = str(gate.get("image", "") or "")
        if img and (os.path.sep in img or ".." in img or img.startswith("/")):
            return False, "gate: image must be a bare filename", {}
        if channel and not re.match(r"^(@[A-Za-z][A-Za-z0-9_]{3,31}|-100\d{5,15})$", channel):
            return False, "gate: channel must look like @mychannel or -100123456789", {}
        if url and not re.match(r"^https://\S{3,300}$", url):
            return False, "gate: join link must start with https://", {}
        if enabled and not url:
            return False, "gate: add the join link (the first button needs somewhere to go)", {}
        if enabled and mode == "verify" and not channel:
            return False, "gate: real checking needs the channel (@name or numeric id)", {}
        out["gate"] = {
            "enabled": enabled, "mode": mode, "channel": channel, "url": url, "image": img,
            "text": _ml(gate.get("text", ""), default_lang),
            "join_label": _ml(gate.get("join_label", ""), default_lang),
            "check_label": _ml(gate.get("check_label", ""), default_lang),
            "fail_text": _ml(gate.get("fail_text", ""), default_lang),
        }

    rmenu = cfg.get("reply_menu")
    if isinstance(rmenu, dict):
        langs = [str(l) for l in (cfg.get("langs") or [default_lang])]
        ok, msg, rm_clean = _clean_reply_menu(rmenu, langs, default_lang, page_ids)
        if not ok:
            return False, msg, {}
        out["reply_menu"] = rm_clean

    rf = cfg.get("referral")
    if isinstance(rf, dict):
        try:
            pct = float(rf.get("percent", 2))
        except (TypeError, ValueError):
            return False, "referrals: the percentage must be a number", {}
        if not (0 <= pct <= 100):
            return False, "referrals: the percentage must be between 0 and 100", {}
        out["referral"] = {"enabled": bool(rf.get("enabled")), "percent": pct}

    db = cfg.get("demo_balance")
    if isinstance(db, dict):
        try:
            lo, hi = float(db.get("min", 3)), float(db.get("max", 14))
        except (TypeError, ValueError):
            return False, "demo balance: min and max must be numbers", {}
        if lo < 0 or hi > 1_000_000 or lo > hi:
            return False, "demo balance: min must be 0 or more and not above max", {}
        out["demo_balance"] = {"enabled": bool(db.get("enabled")), "min": lo, "max": hi}

    if "currency" in cfg:
        ok, msg, cur = currency_util.clean_currency(cfg.get("currency"))
        if not ok:
            return False, "currency: " + msg, {}
        if cur:
            out["currency"] = cur

    lay = cfg.get("_layout")
    if isinstance(lay, dict):
        clean = {}
        for pid, xy in lay.items():
            if pid in page_ids and isinstance(xy, (list, tuple)) and len(xy) == 2:
                try:
                    clean[pid] = [round(float(xy[0])), round(float(xy[1]))]
                except (TypeError, ValueError):
                    pass
        if clean:
            out["_layout"] = clean
    return True, "", out



# ── validation / normalization of an incoming config ───────────────────────
def _validate_config(cfg: dict) -> tuple[bool, str, dict]:
    if not isinstance(cfg, dict):
        return False, "config must be an object", {}
    pages = cfg.get("pages")
    if not isinstance(pages, dict) or not pages:
        return False, "at least one page required", {}
    start = cfg.get("start_page")
    if start not in pages:
        return False, "start_page must reference an existing page", {}

    langs = cfg.get("langs") or ["ka"]
    if not isinstance(langs, list) or not langs:
        langs = ["ka"]
    default_lang = cfg.get("default_lang") or langs[0]

    clean_pages: dict = {}
    for pid, page in pages.items():
        if not _SLUG_RE.match(pid):
            return False, f"invalid page id '{pid}' (use a-z 0-9 _)", {}
        if not isinstance(page, dict):
            return False, f"page '{pid}' malformed", {}

        text = page.get("text", "")
        if isinstance(text, str):
            text = {default_lang: text}
        elif isinstance(text, dict):
            text = {k: str(v) for k, v in text.items()}
        else:
            text = {default_lang: ""}

        rows_in = page.get("rows", [])
        if not isinstance(rows_in, list):
            return False, f"page '{pid}': rows must be a list", {}
        clean_rows = []
        for row in rows_in:
            if not isinstance(row, list):
                return False, f"page '{pid}': each row must be a list", {}
            clean_row = []
            for btn in row:
                if not isinstance(btn, dict):
                    return False, f"page '{pid}': button malformed", {}
                label = btn.get("label", "")
                if isinstance(label, str):
                    label = {default_lang: label}
                elif isinstance(label, dict):
                    label = {k: str(v) for k, v in label.items()}
                else:
                    label = {default_lang: ""}
                action = str(btn.get("action", "noop"))
                if not _ACTION_RE.match(action):
                    return False, f"page '{pid}': invalid action '{action}'", {}
                tgt = _action_target_page(action)
                if tgt is not None and tgt not in pages:
                    return False, f"page '{pid}': button points to missing page '{tgt}'", {}
                if action.startswith("setlang:") and action.split(":")[1] not in langs:
                    return False, f"page '{pid}': language button '{action.split(':')[1]}' is not one of the bot's languages", {}
                if action.startswith("buy:") and action.count(":") == 2 and action.split(":")[2] not in pages:
                    return False, f"page '{pid}': buy button's low-balance screen '{action.split(':')[2]}' does not exist", {}
                if action.split(":", 1)[0] in ("page", "choose", "confirm", "buy") and len(action.encode()) > 58:
                    return False, (f"page '{pid}': button action '{action}' is too long for Telegram (max ~58 characters) - "
                                   "use shorter screen ids"), {}
                clean_btn = {"label": label, "action": action}
                if btn.get("style") in _STYLES:
                    clean_btn["style"] = btn["style"]
                if btn.get("only_variant") in ("land", "building"):
                    clean_btn["only_variant"] = btn["only_variant"]
                if btn.get("always") is True and action.startswith("buy:"):
                    clean_btn["always"] = True
                clean_row.append(clean_btn)
            if clean_row:
                clean_rows.append(clean_row)

        img = str(page.get("image", "") or "")
        if img and (os.path.sep in img or ".." in img or img.startswith("/")):
            return False, f"page '{pid}': image must be a bare filename", {}

        ptype = page.get("type", "message")
        if ptype not in _PAGE_TYPES:
            ptype = "message"

        clean_page = {
            "type": ptype,
            "image": img,
            "parse_mode": page.get("parse_mode") or "HTML",
            "show_admin_button": bool(page.get("show_admin_button", False)),
            "text": text,
            "rows": clean_rows,
        }

        if ptype == "input":
            inp = page.get("input", {}) or {}
            nxt = str(inp.get("next", "") or "")
            if nxt and nxt not in pages:
                return False, f"page '{pid}': input.next points to missing page '{nxt}'", {}
            err = inp.get("error", "")
            if isinstance(err, str):
                err = {default_lang: err}
            elif isinstance(err, dict):
                err = {k: str(v) for k, v in err.items()}
            else:
                err = {default_lang: ""}
            clean_page["input"] = {
                "var": re.sub(r"[^a-zA-Z0-9_]", "", str(inp.get("var", "value")))[:30] or "value",
                "kind": "number" if inp.get("kind") == "number" else "text",
                "min": inp.get("min"),
                "max": inp.get("max"),
                "regex": str(inp.get("regex", "") or "")[:200],
                "error": err,
                "next": nxt,
            }

        if ptype == "receipt":
            rc = page.get("receipt", {}) or {}
            clean_page["receipt"] = {
                "coin_var": re.sub(r"[^a-zA-Z0-9_]", "", str(rc.get("coin_var", "crypto")))[:30] or "crypto",
                "coin_fixed": str(rc.get("coin_fixed", "") or "")[:10],
                "amount_var": re.sub(r"[^a-zA-Z0-9_]", "", str(rc.get("amount_var", "amount")))[:30] or "amount",
                "fiat_to_usd": float(rc.get("fiat_to_usd", 1.0) or 1.0),
                "use_qr": bool(rc.get("use_qr", True)),
                "request_id_var": re.sub(r"[^a-zA-Z0-9_]", "", str(rc.get("request_id_var", "request_id")))[:30] or "request_id",
            }

        if ptype == "list":
            tgt = str(page.get("item_target", "") or "")
            if tgt and tgt not in pages:
                return False, f"page '{pid}': list target page '{tgt}' does not exist", {}
            src = page.get("source", "products")
            clean_page["source"] = src if src in _SOURCES else "products"
            clean_page["city"] = re.sub(r"[^a-zA-Z0-9_]", "", str(page.get("city", "tbilisi")))[:30] or "tbilisi"
            clean_page["item_var"] = re.sub(r"[^a-zA-Z0-9_]", "", str(page.get("item_var", "product")))[:30] or "product"
            clean_page["item_target"] = tgt
            for key in ("city_var", "category_var", "product_var"):
                val = _VAR_RE.sub("", str(page.get(key, "") or ""))[:30]
                if val:
                    clean_page[key] = val
            if page.get("hide_empty") is False:
                clean_page["hide_empty"] = False

        if ptype == "history" or isinstance(page.get("history"), dict):
            hs = page.get("history") if isinstance(page.get("history"), dict) else {}
            try:
                limit = max(1, min(int(hs.get("limit", 10)), 30))
            except (TypeError, ValueError):
                limit = 10
            clean_page["history"] = {
                "limit": limit,
                "line": _ml(hs.get("line", ""), default_lang),
                "empty": _ml(hs.get("empty", ""), default_lang),
            }
            fk = hs.get("fake")
            if isinstance(fk, dict):
                links = []
                for l in (fk.get("links") or [])[:30]:
                    l = str(l).strip()
                    if l and re.match(r"^https?://\S{3,500}$", l) and l not in links:
                        links.append(l)
                if fk.get("enabled") and not links:
                    return False, f"page '{pid}': demo purchases need at least one link starting with http(s)://", {}
                try:
                    fmin = max(1, min(int(fk.get("min", 1)), 30))
                    fmax = max(fmin, min(int(fk.get("max", 3)), 30))
                except (TypeError, ValueError):
                    fmin, fmax = 1, 3
                clean_page["history"]["fake"] = {"enabled": bool(fk.get("enabled")), "links": links, "min": fmin, "max": fmax,
                                                 "line": _ml(fk.get("line", ""), default_lang)}

        if page.get("image_from") in ("district",):
            clean_page["image_from"] = page["image_from"]

        title = str(page.get("title", "") or "")[:60]
        if title:
            clean_page["title"] = title

        hook = page.get("on_enter")
        if isinstance(hook, dict):
            clean_hook = _clean_hook(hook, default_lang)
            if clean_hook is None:
                return False, f"page '{pid}': invalid on_enter image name", {}
            if clean_hook:
                clean_page["on_enter"] = clean_hook

        # optional item lookup on any page (merges product/district fields into vars)
        il = page.get("item_lookup")
        if isinstance(il, dict) and il.get("source"):
            clean_page["item_lookup"] = {
                "source": il["source"] if il["source"] in _SOURCES else "products",
                "city": re.sub(r"[^a-zA-Z0-9_]", "", str(il.get("city", "tbilisi")))[:30] or "tbilisi",
                "var": re.sub(r"[^a-zA-Z0-9_]", "", str(il.get("var", "product")))[:30] or "product",
            }

        clean_pages[pid] = clean_page

    out = {
        "version": 2,
        "start_page": start,
        "default_lang": default_lang,
        "langs": langs,
        "pages": clean_pages,
    }
    ok, msg, extras = _clean_extras(cfg, default_lang, set(clean_pages))
    if not ok:
        return False, msg, {}
    out.update(extras)
    return True, "", out


# ── routes ─────────────────────────────────────────────────────────────────
@constructor_bp.route("/bot/<name>/constructor")
@login_required
def constructor_page(name):
    bot = _bot_or_none(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))
    return render_template(
        "constructor.html",
        name=name,
        display_name=bot.get("display_name", name),
        service=bot.get("service", name),
    )


@constructor_bp.route("/bot/<name>/api/menu-config", methods=["GET"])
@login_required
def get_menu_config(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    path = _config_path(bot)
    if path.exists():
        try:
            return jsonify(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            return jsonify({"error": f"corrupt config: {e}", "config": _default_config()}), 200
    return jsonify(_default_config())


@constructor_bp.route("/bot/<name>/api/features", methods=["GET"])
@login_required
def bot_features(name):
    """What this bot's engine can do — older bots keep their own copy of the engine."""
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    try:
        engine = (_bot_dir(bot) / "menu_engine.py").read_text(encoding="utf-8")
    except OSError:
        engine = ""
    return jsonify({"reply_menu": "REPLY_MENU_SUPPORT" in engine})


@constructor_bp.route("/bot/<name>/api/menu-config", methods=["POST"])
@login_required
def save_menu_config(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404

    raw = request.get_data()
    if len(raw) > MAX_CONFIG_BYTES:
        return jsonify({"error": "config too large"}), 413
    try:
        incoming = json.loads(raw.decode("utf-8"))
    except Exception as e:
        return jsonify({"error": f"invalid JSON: {e}"}), 400

    ok, msg, clean = _validate_config(incoming)
    if not ok:
        return jsonify({"error": msg}), 400

    path = _config_path(bot)
    _backup_config(bot)
    # atomic write
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return jsonify({"ok": True})


@constructor_bp.route("/bot/<name>/api/images", methods=["GET"])
@login_required
def list_images(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    d = _bot_dir(bot)
    imgs = []
    if d.exists():
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                imgs.append({"name": f.name, "size": f.stat().st_size, "kind": _media_kind(f.name)})
    return jsonify({"images": imgs})


@constructor_bp.route("/bot/<name>/api/images", methods=["POST"])
@login_required
def upload_image(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400
    f = request.files["file"]
    fname = secure_filename(f.filename or "")
    if not fname:
        return jsonify({"error": "bad filename"}), 400
    ext = os.path.splitext(fname)[1].lower()
    if ext not in IMAGE_EXTS:
        return jsonify({"error": f"unsupported type {ext}"}), 400
    blob = f.read()
    if ext in VIDEO_EXTS:
        if len(blob) > MAX_VIDEO_BYTES:
            return jsonify({"error": "video too large (max 48 MB)"}), 413
    elif len(blob) > MAX_IMAGE_BYTES:
        return jsonify({"error": "image too large (max 8 MB)"}), 413
    dest = _bot_dir(bot) / fname
    # keep it inside the bot dir
    try:
        dest.resolve().relative_to(_bot_dir(bot).resolve())
    except ValueError:
        return jsonify({"error": "invalid path"}), 400
    dest.write_bytes(blob)
    return jsonify({"ok": True, "name": fname})


@constructor_bp.route("/bot/<name>/api/image/<path:fname>", methods=["GET"])
@login_required
def get_image(name, fname):
    """Serve a bot image so the constructor can preview it."""
    bot = _bot_or_none(name)
    if not bot:
        return "", 404
    safe = secure_filename(fname)
    path = _bot_dir(bot) / safe
    try:
        path.resolve().relative_to(_bot_dir(bot).resolve())
    except ValueError:
        return "", 400
    if not path.exists() or path.suffix.lower() not in IMAGE_EXTS:
        return "", 404
    from flask import send_file
    return send_file(str(path))


# ── env editor (tokens / admins / wallets) ─────────────────────────────────
_ENV_KNOWN = ["BOT_TOKENS", "ADMIN_IDS", "BTC_WALLET", "LTC_WALLET", "USDT_WALLET"]
_WALLET_KEY = re.compile(r"^([A-Z0-9]{2,10})_WALLET$")


def _parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _write_env(bot_dir: Path, env: dict) -> None:
    keys = [k for k in _ENV_KNOWN if k in env]
    keys += [k for k in env if k not in _ENV_KNOWN and k != "BOT_TOKEN"]
    env_path = bot_dir / ".env"
    env_path.write_text("\n".join(f"{k}={env[k]}" for k in keys) + "\n")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass


@constructor_bp.route("/bot/<name>/api/env", methods=["GET"])
@login_required
def get_env(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    env_path = _bot_dir(bot) / ".env"
    env = _parse_env(env_path.read_text()) if env_path.exists() else {}
    tokens = [t.strip() for t in env.get("BOT_TOKENS", env.get("BOT_TOKEN", "")).split(",") if t.strip()]
    admins = [a.strip() for a in env.get("ADMIN_IDS", "").split(",") if a.strip()]
    wallets = {m.group(1): v for k, v in env.items() if (m := _WALLET_KEY.match(k))}
    return jsonify({
        "tokens": tokens,
        "admins": admins,
        "wallets": wallets,
        "btc": env.get("BTC_WALLET", ""),
        "ltc": env.get("LTC_WALLET", ""),
        "usdt": env.get("USDT_WALLET", ""),
    })


@constructor_bp.route("/bot/<name>/api/env", methods=["POST"])
@login_required
def save_env(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True, silent=True) or {}

    tokens = [str(t).strip() for t in data.get("tokens", []) if str(t).strip()]
    admins = [str(a).strip() for a in data.get("admins", []) if str(a).strip().isdigit()]

    env_path = _bot_dir(bot) / ".env"
    env = _parse_env(env_path.read_text()) if env_path.exists() else {}
    env["BOT_TOKENS"] = ",".join(tokens)
    env["ADMIN_IDS"] = ",".join(admins)

    wallets = data.get("wallets")
    if not isinstance(wallets, dict):   # legacy payload
        wallets = {"BTC": data.get("btc", ""), "LTC": data.get("ltc", ""), "USDT": data.get("usdt", "")}
    for code, addr in wallets.items():
        code = str(code).strip().upper()
        if _CODE_RE.match(code):
            env[f"{code}_WALLET"] = re.sub(r"\s", "", str(addr))
    for k in ("BTC_WALLET", "LTC_WALLET", "USDT_WALLET"):
        env.setdefault(k, "")
    _write_env(_bot_dir(bot), env)
    return jsonify({"ok": True})


@constructor_bp.route("/bot/<name>/api/restart", methods=["POST"])
@login_required
def restart_bot(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    service = bot.get("service", name)
    env_path = _bot_dir(bot) / ".env"
    env = _parse_env(env_path.read_text()) if env_path.exists() else {}
    if not (env.get("BOT_TOKENS") or env.get("BOT_TOKEN") or "").strip():
        return jsonify({"error": "Add a bot token first (Settings → Bot & admins) — the bot can't run without one."}), 400
    subprocess.run(["systemctl", "enable", service], capture_output=True, timeout=20)
    r = subprocess.run(["systemctl", "restart", service],
                       capture_output=True, text=True, timeout=20)
    if r.returncode == 0:
        return jsonify({"ok": True})
    return jsonify({"error": r.stderr or "restart failed"}), 500


# ══════════════════════════════════════════════════════════════════════════
#  Builder helpers: history, system strings, tokens, status, preview data
# ══════════════════════════════════════════════════════════════════════════
import threading
import time
import urllib.request
import uuid
from datetime import datetime

KEEP_BACKUPS = 30
TEMPLATE_REPO = Path(os.environ.get("TEMPLATE_REPO", "/opt/automated-bot-repo"))


def _history_dir(bot: dict) -> Path:
    return _bot_dir(bot) / "menu_config_history"


def _backup_config(bot: dict) -> None:
    """Keep the previous published config so a bad publish can be rolled back."""
    path = _config_path(bot)
    if not path.exists():
        return
    d = _history_dir(bot)
    d.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (d / f"{stamp}.json").write_bytes(path.read_bytes())
    for old in sorted(d.glob("*.json"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)


@constructor_bp.route("/bot/<name>/api/history", methods=["GET"])
@login_required
def config_history(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    d = _history_dir(bot)
    items = []
    if d.exists():
        for f in sorted(d.glob("*.json"), reverse=True):
            try:
                cfg = json.loads(f.read_text(encoding="utf-8"))
                items.append({"id": f.stem, "pages": len(cfg.get("pages", {})), "size": f.stat().st_size})
            except Exception:
                continue
    return jsonify({"items": items})


@constructor_bp.route("/bot/<name>/api/history/<hid>", methods=["GET"])
@login_required
def config_history_get(name, hid):
    bot = _bot_or_none(name)
    if not bot or not re.match(r"^[0-9\-]{15}$", hid):
        return jsonify({"error": "not found"}), 404
    f = _history_dir(bot) / f"{hid}.json"
    if not f.exists():
        return jsonify({"error": "not found"}), 404
    return jsonify(json.loads(f.read_text(encoding="utf-8")))


@constructor_bp.route("/bot/<name>/api/system-strings", methods=["GET"])
@login_required
def system_strings(name):
    """Built-in bot messages (texts.py) that can be overridden: {key: {lang: default}}."""
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    tp = _bot_dir(bot) / "texts.py"
    out: dict = {}
    if tp.exists():
        try:
            # run texts.py in a separate process (it is bot code, not panel code)
            code = ("import json,runpy,sys;"
                    "tr=runpy.run_path(sys.argv[1]).get('TRANSLATIONS',{});"
                    "print(json.dumps({l:{k:v for k,v in e.items() if isinstance(v,str)} for l,e in tr.items()}))")
            r = subprocess.run(["python3", "-I", "-c", code, str(tp)], capture_output=True, text=True,
                               timeout=15, cwd=str(_bot_dir(bot)))
            tr = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
            for lang, entries in tr.items():
                for key, val in entries.items():
                    if isinstance(val, str):
                        out.setdefault(key, {})[lang] = val
        except Exception:
            out = {}
    return jsonify({"strings": out})


def _telegram_me(token: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=8) as r:
            data = json.loads(r.read().decode())
        return data["result"] if data.get("ok") else None
    except Exception:
        return None


@constructor_bp.route("/constructor/api/check-tokens", methods=["POST"])
@login_required
def check_tokens():
    tokens = [str(t).strip() for t in (request.get_json(force=True, silent=True) or {}).get("tokens", [])]
    results = []
    for t in tokens[:20]:
        if not t:
            continue
        me = _telegram_me(t) if re.match(r"^\d{6,}:[\w-]{20,}$", t) else None
        results.append({"token": t, "ok": bool(me), "username": (me or {}).get("username", "")})
    return jsonify({"results": results})


@constructor_bp.route("/bot/<name>/api/gate-test", methods=["POST"])
@login_required
def gate_test(name):
    """Can this bot really check membership of the channel? (bot must be a channel admin)"""
    import urllib.request, urllib.parse
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    channel = str((request.get_json(silent=True) or {}).get("channel", "")).strip()
    if not re.match(r"^(@[A-Za-z][A-Za-z0-9_]{3,31}|-100\d{5,15})$", channel):
        return jsonify({"ok": False, "msg": "Channel must look like @mychannel or -100123456789"})
    env_path = _bot_dir(bot) / ".env"
    env = _parse_env(env_path.read_text(encoding="utf-8")) if env_path.exists() else {}
    tokens = [t.strip() for t in env.get("BOT_TOKENS", "").split(",") if t.strip()]
    if not tokens:
        return jsonify({"ok": False, "msg": "Add a bot token in Settings first - the bot has to look at the channel."})

    def call(method, **params):
        url = f"https://api.telegram.org/bot{tokens[0]}/{method}?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except Exception:
                return {"ok": False, "description": str(e)}
        except Exception as e:
            return {"ok": False, "description": str(e)}

    me = call("getMe")
    if not me.get("ok"):
        return jsonify({"ok": False, "msg": "The bot token is not valid."})
    chat = call("getChat", chat_id=channel)
    if not chat.get("ok"):
        return jsonify({"ok": False, "msg": f"Bot can't see {channel}: add @{me['result'].get('username','the bot')} to the channel as an administrator."})
    mem = call("getChatMember", chat_id=channel, user_id=me["result"]["id"])
    status = (mem.get("result") or {}).get("status")
    title = chat["result"].get("title", channel)
    if status in ("administrator", "creator"):
        return jsonify({"ok": True, "msg": f"Works: the bot is an admin of \u201c{title}\u201d and can check members."})
    return jsonify({"ok": False, "msg": f"The bot is in \u201c{title}\u201d but is not an administrator - make it one so it can check members."})


@constructor_bp.route("/bot/<name>/api/flow-recipe", methods=["GET"])
@login_required
def flow_recipe_api(name):
    """Screens of a ready-made flow, ids made unique against the ones the builder already has."""
    if not _bot_or_none(name):
        return jsonify({"error": "not found"}), 404
    kind = request.args.get("kind", "categories")
    if kind not in ("classic", "categories", "language"):
        return jsonify({"error": "unknown flow"}), 400
    langs = [l for l in re.split(r"[,\s]+", request.args.get("langs", "en")) if l in ("en", "ka", "ru")] or ["en"]
    taken = {t for t in request.args.get("taken", "").split(",") if _SLUG_RE.match(t)}
    back = request.args.get("back", "main")
    back = back if _SLUG_RE.match(back) else "main"
    if kind == "language":
        names = {"en": "🇬🇧 English", "ka": "🇬🇪 ქართული", "ru": "🇷🇺 Русский"}
        pid, n = "language", 2
        while pid in taken:
            pid, n = f"language_{n}", n + 1
        ml = lambda en, ka, ru: {l: {"en": en, "ka": ka, "ru": ru}[l] for l in langs}
        pages = {pid: {"type": "message", "image": "", "parse_mode": "HTML", "show_admin_button": False, "title": "Language",
                       "text": ml("🌐 Choose your language", "🌐 აირჩიეთ ენა", "🌐 Выберите язык"),
                       "rows": [[{"label": {l: names[code] for l in langs}, "action": f"setlang:{code}:{back}"}] for code in langs]}}
        label = ml("🌐 Language", "🌐 ენა", "🌐 Язык")
        return jsonify({"pages": pages, "layout": {pid: [0, 0]}, "entry": pid, "entry_label": label})
    pages, layout, entry = flow_recipe(kind, langs, taken, back)
    return jsonify({"pages": pages, "layout": layout, "entry": entry,
                    "entry_label": {l: {"en": "🛍 Shop", "ka": "🛍 მაღაზია", "ru": "🛍 Магазин"}.get(l, "🛍 Shop") for l in langs}})


@constructor_bp.route("/bot/<name>/api/currency-rate", methods=["GET"])
@login_required
def currency_rate(name):
    if not _bot_or_none(name):
        return jsonify({"error": "not found"}), 404
    code = request.args.get("code", "").upper()
    if not re.match(r"^[A-Z]{3}$", code):
        return jsonify({"ok": False, "msg": "Enter a 3-letter currency code, e.g. GEL"})
    rate, src = currency_util.fetch_usd_per_unit(code)
    if rate is None:
        return jsonify({"ok": False, "msg": src + " — set a fixed rate instead."})
    return jsonify({"ok": True, "usd_per_unit": rate, "source": src, "msg": f"1 {code} = {rate:.4f} USD (live, {src})"})


@constructor_bp.route("/bot/<name>/api/status", methods=["GET"])
@login_required
def bot_status(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    r = subprocess.run(["systemctl", "is-active", bot.get("service", name)],
                       capture_output=True, text=True, timeout=10)
    return jsonify({"active": r.stdout.strip() == "active", "state": r.stdout.strip()})


@constructor_bp.route("/bot/<name>/api/preview-data", methods=["GET"])
@login_required
def preview_data(name):
    """Products / districts so the builder preview can render list pages."""
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    dj = _bot_dir(bot) / "data.json"
    prods, dists, cities, cats = [], [], set(), []
    if dj.exists():
        try:
            d = json.loads(dj.read_text(encoding="utf-8"))
            prods = [{"name": p.get("name", k), "price_usd": p.get("price_usd")}
                     for k, p in d.get("products", {}).items() if p.get("owner") is None][:12]
            dists = [{"id": k, "name": p.get("name", k), "image": p.get("image", ""), "city": p.get("city", "")}
                     for k, p in d.get("districts", {}).items()][:60]
            cats = [{"name": c.get("name", k)} for k, c in d.get("categories", {}).items()][:12]
            cities = {str(p.get("city") or "tbilisi") for p in d.get("products", {}).values() if p.get("owner") is None}
            cities |= {str(x.get("city") or "tbilisi") for x in d.get("districts", {}).values()}
        except Exception:
            pass
    return jsonify({"products": prods, "districts": dists, "cities": max(len(cities), 1),
                    "city_names": sorted(cities), "categories": cats})


@constructor_bp.route("/bot/<name>/api/images/<path:fname>", methods=["DELETE"])
@login_required
def delete_image(name, fname):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    safe = secure_filename(fname)
    path = _bot_dir(bot) / safe
    if path.suffix.lower() not in IMAGE_EXTS or not path.exists():
        return jsonify({"error": "not found"}), 404
    # refuse to delete an image a page still uses
    cfgp = _config_path(bot)
    if cfgp.exists() and f'"{safe}"' in cfgp.read_text(encoding="utf-8"):
        return jsonify({"error": "this image is used by a screen — change the screen first"}), 409
    path.unlink()
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════════
#  Hub + new-bot wizard
# ══════════════════════════════════════════════════════════════════════════
def _is_engine_bot(bot: dict) -> bool:
    return bool(bot.get("engine")) or (_bot_dir(bot) / "menu_engine.py").exists()


@constructor_bp.route("/constructor")
@login_required
def constructor_hub():
    bots = []
    for name, bot in _load_bots().get("bots", {}).items():
        if not _is_engine_bot(bot):
            continue
        cfg_pages = 0
        try:
            cfg_pages = len(json.loads(_config_path(bot).read_text(encoding="utf-8")).get("pages", {}))
        except Exception:
            pass
        r = subprocess.run(["systemctl", "is-active", bot.get("service", name)],
                           capture_output=True, text=True, timeout=10)
        bots.append({"name": name, "display": bot.get("display_name", name),
                     "pages": cfg_pages, "state": r.stdout.strip()})
    return render_template("constructor_hub.html", bots=bots)


_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,38}$")


_MONEY_SIGN = re.compile(r"(\{(?:balance|price|product_price|amount|purchase_total|earned)\})\s*\$|\$\s*(\{(?:balance|price|product_price|amount|purchase_total|earned)\})")


def _strip_money_signs(node):
    """Remove hard-coded "$" next to money values; the bot's currency adds its own symbol."""
    if isinstance(node, str):
        return _MONEY_SIGN.sub(lambda m: m.group(1) or m.group(2), node)
    if isinstance(node, list):
        return [_strip_money_signs(x) for x in node]
    if isinstance(node, dict):
        return {k: _strip_money_signs(v) for k, v in node.items()}
    return node


def default_reply_menu(langs: list[str], start_page: str) -> dict:
    """A working starting point for the bottom menu: one button back to the start screen."""
    def ml(en, ka, ru):
        table = {"en": en, "ka": ka, "ru": ru}
        return {l: table.get(l, en) for l in langs}
    return {
        "enabled": True,
        "placeholder": ml("Menu", "მენიუ", "Меню"),
        "intro": ml("👇 Use the menu below", "👇 გამოიყენეთ ქვედა მენიუ", "👇 Используйте меню ниже"),
        "rows": [[{"label": ml("🏠 Main menu", "🏠 მთავარი მენიუ", "🏠 Главное меню"), "target": start_page}]],
    }


def starter_config(kind: str, langs: list[str], _internal: bool = False) -> dict:
    """Ready-made menu_config for the wizard ('blank' or 'shop')."""
    def ml(en, ka, ru):
        table = {"en": en, "ka": ka, "ru": ru}
        return {l: table.get(l, en) for l in langs}

    back = {"label": ml("⬅️ Back", "⬅️ უკან", "⬅️ Назад"), "action": "page:main"}
    if kind == "blank":
        return {
            "version": 2, "start_page": "main", "default_lang": langs[0], "langs": langs,
            "pages": {"main": {"type": "message", "image": "", "parse_mode": "HTML",
                               "show_admin_button": True,
                               "text": ml("Welcome! Edit this screen in the constructor.",
                                          "მოგესალმებით! შეცვალეთ ეს ეკრანი კონსტრუქტორში.",
                                          "Добро пожаловать! Измените этот экран в конструкторе."),
                               "rows": []}},
            "_layout": {"main": [80, 80]},
        }
    if kind in ("shop", "shop_categories") and not _internal:
        cfg = starter_config(kind, langs, _internal=True)
        cfg = _strip_money_signs(cfg)
        cfg["currency"] = {"code": "USD", "symbol": "$", "position": "after", "space": False, "rate": "auto"}
        return cfg
    if kind == "shop_categories":
        cfg = starter_config("shop", langs, _internal=True)
        for gone in ("products", "product_detail", "product_receipt"):
            cfg["pages"].pop(gone, None)
            cfg["_layout"].pop(gone, None)
        pages, layout, entry = flow_recipe("categories", langs, set(cfg["pages"]), "main")
        cfg["pages"].update(pages)
        for i, (pid, xy) in enumerate(layout.items()):
            cfg["_layout"][pid] = [380 + xy[0], 40 + (i % 2) * 0]
        for row in cfg["pages"]["main"]["rows"]:
            for b in row:
                if b["action"] == "page:products":
                    b["action"] = f"page:{entry}"
        return cfg
    coin_rows = lambda target, kind="price": [[{"label": {l: f"{c}: {{{code}_{kind}}}" for l in langs}, "action": f"choose:crypto:{code}:{target}"}]
                                              for code, c in (("BTC", "Bitcoin"), ("LTC", "Litecoin"), ("USDT", "USDT TRC20"))]
    return {
        "version": 2, "start_page": "main", "default_lang": langs[0], "langs": langs,
        "pages": {
            "main": {"type": "message", "image": "", "parse_mode": "HTML", "show_admin_button": True,
                     "text": ml("🛍 Welcome to the shop!\n\n💵 Balance: {balance} $",
                                "🛍 კეთილი იყოს თქვენი მობრძანება!\n\n💵 ბალანსი: {balance} $",
                                "🛍 Добро пожаловать в магазин!\n\n💵 Баланс: {balance} $"),
                     "rows": [[{"label": ml("🛍 Products", "🛍 პროდუქტები", "🛍 Товары"), "action": "page:products"}],
                              [{"label": ml("💵 Top up balance", "💵 ბალანსის შევსება", "💵 Пополнить баланс"), "action": "page:topup_amount"},
                               {"label": ml("🧾 My purchases", "🧾 ჩემი შესყიდვები", "🧾 Мои покупки"), "action": "page:purchases"}],
                              [{"label": ml("🌐 Language", "🌐 ენა", "🌐 Язык"), "action": "builtin:language"}]]},
            "products": {"type": "list", "image": "", "parse_mode": "HTML", "source": "products", "city": "tbilisi",
                         "item_var": "product", "item_target": "product_detail", "show_admin_button": False,
                         "text": ml("Choose a product:", "აირჩიეთ პროდუქტი:", "Выберите товар:"), "rows": [[back]]},
            "product_detail": {"type": "message", "image": "", "parse_mode": "HTML",
                               "item_lookup": {"source": "products", "city": "tbilisi", "var": "product"},
                               "text": ml("<b>{name}</b>\n{description}\n\nPrice: {price} $\nChoose how to pay:",
                                          "<b>{name}</b>\n{description}\n\nფასი: {price} $\nაირჩიეთ გადახდის მეთოდი:",
                                          "<b>{name}</b>\n{description}\n\nЦена: {price} $\nВыберите способ оплаты:"),
                               "rows": coin_rows("product_receipt") + [[{"label": back["label"], "action": "page:products"}]]},
            "product_receipt": {"type": "receipt", "image": "", "parse_mode": "HTML",
                                "item_lookup": {"source": "products", "city": "tbilisi", "var": "product"},
                                "receipt": {"coin_var": "crypto", "coin_fixed": "", "amount_var": "price",
                                            "fiat_to_usd": 1.0, "use_qr": True, "request_id_var": "request_id"},
                                "text": ml("Order #{request_id}\n<b>{name}</b>\n\nSend <code>{crypto_amount}</code> {coin} to:\n<code>{wallet}</code>\n\nThen press the button below.",
                                           "შეკვეთა #{request_id}\n<b>{name}</b>\n\nგადარიცხეთ <code>{crypto_amount}</code> {coin}:\n<code>{wallet}</code>\n\nშემდეგ დააჭირეთ ღილაკს.",
                                           "Заказ #{request_id}\n<b>{name}</b>\n\nОтправьте <code>{crypto_amount}</code> {coin} на:\n<code>{wallet}</code>\n\nЗатем нажмите кнопку."),
                                "rows": [[{"label": ml("✅ I paid", "✅ გადავიხადე", "✅ Я оплатил"), "action": "confirm:paid_done"}],
                                         [{"label": back["label"], "action": "page:products"}]]},
            "purchases": {"type": "history", "image": "", "parse_mode": "HTML",
                          "history": {"limit": 10, "line": {}, "empty": {}},
                          "text": ml("🧾 <b>Your purchases</b>\n\n{purchases}",
                                     "🧾 <b>თქვენი შესყიდვები</b>\n\n{purchases}",
                                     "🧾 <b>Ваши покупки</b>\n\n{purchases}"),
                          "rows": [[back]]},
            "paid_done": {"type": "message", "image": "", "parse_mode": "HTML",
                          "text": ml("Thank you! An admin will check your payment shortly.",
                                     "გმადლობთ! ადმინისტრატორი მალე შეამოწმებს გადახდას.",
                                     "Спасибо! Администратор скоро проверит оплату."),
                          "rows": [[back]]},
            "topup_amount": {"type": "input", "image": "", "parse_mode": "HTML",
                             "input": {"var": "amount", "kind": "number", "min": 5, "max": 3000, "regex": "",
                                       "error": ml("❌ Invalid amount (5–3000).", "❌ არასწორი თანხა (5–3000).", "❌ Неверная сумма (5–3000)."),
                                       "next": "topup_crypto"},
                             "text": ml("Enter the amount to add (5–3000):", "შეიყვანეთ თანხა (5–3000):", "Введите сумму (5–3000):"),
                             "rows": [[back]]},
            "topup_crypto": {"type": "message", "image": "", "parse_mode": "HTML",
                             "text": ml("Choose a coin:", "აირჩიეთ მონეტა:", "Выберите монету:"),
                             "rows": coin_rows("topup_receipt", "amount") + [[back]]},
            "topup_receipt": {"type": "receipt", "image": "", "parse_mode": "HTML",
                              "receipt": {"coin_var": "crypto", "coin_fixed": "", "amount_var": "amount",
                                          "fiat_to_usd": 1.0, "use_qr": True, "request_id_var": "request_id"},
                              "text": ml("Top-up #{request_id}\n\nSend <code>{crypto_amount}</code> {coin} to:\n<code>{wallet}</code>",
                                         "შევსება #{request_id}\n\nგადარიცხეთ <code>{crypto_amount}</code> {coin}:\n<code>{wallet}</code>",
                                         "Пополнение #{request_id}\n\nОтправьте <code>{crypto_amount}</code> {coin} на:\n<code>{wallet}</code>"),
                              "rows": [[{"label": ml("✅ I paid", "✅ გადავიხადე", "✅ Я оплатил"), "action": "confirm:paid_done"}],
                                       [back]]},
        },
        "_layout": {"main": [60, 120], "products": [380, 40], "product_detail": [700, 40], "product_receipt": [1020, 40],
                    "paid_done": [1340, 80], "purchases": [380, 520], "topup_amount": [380, 300], "topup_crypto": [700, 300], "topup_receipt": [1020, 300]},
    }


def flow_recipe(kind: str, langs: list[str], taken: set, back: str = "main") -> tuple[dict, dict, str]:
    """Ready-made shop flow wired from existing building blocks.

    kind "classic":    products -> district -> payment
    kind "categories": city -> category -> listing -> district -> variant -> payment
    Returns (pages, layout, entry_page_id); ids that are already `taken` get a numeric suffix.
    """
    def ml(en, ka, ru):
        table = {"en": en, "ka": ka, "ru": ru}
        return {l: table.get(l, en) for l in langs}

    used = set(taken)

    def uid(base):
        n, cand = 2, base
        while cand in used:
            cand = f"{base}_{n}"
            n += 1
        used.add(cand)
        return cand

    full = kind == "categories"
    ids = {"cities": uid("cities") if full else None, "categories": uid("categories") if full else None,
           "listing": uid("listing" if full else "products"), "districts": uid("districts"),
           "variants": uid("variants") if full else None, "pay": uid("pay_method"),
           "receipt": uid("order_receipt"), "paid": uid("order_paid"), "delivered": uid("order_delivered")}
    back_label = ml("⬅️ Back", "⬅️ უკან", "⬅️ Назад")
    bk = lambda target: [{"label": back_label, "action": f"page:{target}"}]
    first = ids["cities"] or ids["listing"]
    pages: dict = {}

    def lst(source, text, target, back_to, title, **extra):
        pg = {"type": "list", "image": "", "parse_mode": "HTML", "source": source, "city": "tbilisi",
              "item_var": extra.pop("item_var"), "item_target": target, "show_admin_button": False,
              "title": title, "text": text, "rows": [bk(back_to)]}
        pg.update(extra)
        return pg

    if full:
        pages[ids["cities"]] = lst("cities", ml("🏙 Choose a city:", "🏙 აირჩიეთ ქალაქი:", "🏙 Выберите город:"),
                                   ids["categories"], back, "Cities", item_var="city")
        pages[ids["categories"]] = lst("categories", ml("🗂 Choose a category:", "🗂 აირჩიეთ კატეგორია:", "🗂 Выберите категорию:"),
                                       ids["listing"], ids["cities"], "Categories", item_var="category", city_var="city")
        pages[ids["listing"]] = lst("products", ml("🛍 Choose a listing:", "🛍 აირჩიეთ პროდუქტი:", "🛍 Выберите товар:"),
                                    ids["districts"], ids["categories"], "Listing", item_var="product",
                                    city_var="city", category_var="category")
    else:
        pages[ids["listing"]] = lst("products", ml("🛍 Choose a product:", "🛍 აირჩიეთ პროდუქტი:", "🛍 Выберите товар:"),
                                    ids["districts"], back, "Products", item_var="product")
    after_district = ids["variants"] or ids["pay"]
    pages[ids["districts"]] = lst(
        "districts",
        ml("<b>{name}</b>\n{description}\n💵 {price} $\n\n📍 Choose a district:",
           "<b>{name}</b>\n{description}\n💵 {price} $\n\n📍 აირჩიეთ უბანი:",
           "<b>{name}</b>\n{description}\n💵 {price} $\n\n📍 Выберите район:"),
        after_district, ids["listing"], "Districts", item_var="district", product_var="product",
        item_lookup={"source": "products", "city": "tbilisi", "var": "product"},
        **({"city_var": "city"} if full else {}))
    if full:
        vbtn = lambda key, label: [{"label": {l: label for l in langs}, "only_variant": key, "action": f"choose:variant:@:{ids['pay']}"}]
        pages[ids["variants"]] = {
            "type": "message", "image": "", "parse_mode": "HTML", "show_admin_button": False, "title": "Variants",
            "text": ml("Choose a variant:", "აირჩიეთ ვარიანტი:", "Выберите вариант:"),
            "rows": [vbtn("land", "მიწა უბანი / BURRIED IN DISTRICT"), vbtn("building", "კორპუსი / BUILDING"), bk(ids["districts"])]}
    coin_rows = [[{"label": {l: f"{c}: {{{code}_price}}" for l in langs}, "action": f"choose:crypto:{code}:{ids['receipt']}"}]
                 for code, c in (("BTC", "Bitcoin"), ("LTC", "Litecoin"), ("USDT", "USDT TRC20"))]
    pages[ids["pay"]] = {"type": "message", "image": "", "image_from": "district", "parse_mode": "HTML", "show_admin_button": False, "title": "Payment method",
                         "text": ml("Choose how to pay:", "აირჩიეთ გადახდის მეთოდი:", "Выберите способ оплаты:"),
                         "rows": [[{"label": ml("💰 Pay with balance ({balance})", "💰 ბალანსით გადახდა ({balance})", "💰 Оплатить балансом ({balance})"),
                                    "action": f"buy:{ids['delivered']}"}]] + coin_rows + [bk(ids["variants"] or ids["districts"])]}
    pages[ids["delivered"]] = {"type": "message", "image": "", "parse_mode": "HTML", "show_admin_button": False, "title": "Bought with balance",
                               "text": ml("✅ Purchase complete!\n💵 Balance: {balance}\n\nYour order is delivered in the next message.",
                                          "✅ ყიდვა წარმატებით შესრულდა!\n💵 ბალანსი: {balance}\n\nშეკვეთა მომდევნო შეტყობინებაშია.",
                                          "✅ Покупка выполнена!\n💵 Баланс: {balance}\n\nЗаказ придёт следующим сообщением."),
                               "rows": [bk(back)]}
    variant_en, variant_ka, variant_ru = ("\n🔹 {variant}",) * 3 if full else ("", "", "")
    pages[ids["receipt"]] = {
        "type": "receipt", "image": "", "parse_mode": "HTML", "show_admin_button": False, "title": "Order receipt",
        "item_lookup": {"source": "products", "city": "tbilisi", "var": "product"},
        "receipt": {"coin_var": "crypto", "coin_fixed": "", "amount_var": "price", "fiat_to_usd": 1.0,
                    "use_qr": True, "request_id_var": "request_id"},
        "text": ml("Order #{request_id}\n<b>{name}</b>\n📍 {district_name}" + variant_en + "\n\nSend <code>{crypto_amount}</code> {coin} to:\n<code>{wallet}</code>\n\nThen press the button below.",
                   "შეკვეთა #{request_id}\n<b>{name}</b>\n📍 {district_name}" + variant_ka + "\n\nგადარიცხეთ <code>{crypto_amount}</code> {coin}:\n<code>{wallet}</code>\n\nშემდეგ დააჭირეთ ღილაკს.",
                   "Заказ #{request_id}\n<b>{name}</b>\n📍 {district_name}" + variant_ru + "\n\nОтправьте <code>{crypto_amount}</code> {coin} на:\n<code>{wallet}</code>\n\nЗатем нажмите кнопку."),
        "rows": [[{"label": ml("✅ I paid", "✅ გადავიხადე", "✅ Я оплатил"), "action": f"confirm:{ids['paid']}"}],
                 bk(ids["pay"])]}
    pages[ids["paid"]] = {"type": "message", "image": "", "parse_mode": "HTML", "show_admin_button": False, "title": "Order sent",
                          "text": ml("Thank you! An admin will check your payment shortly.",
                                     "გმადლობთ! ადმინისტრატორი მალე შეამოწმებს გადახდას.",
                                     "Спасибо! Администратор скоро проверит оплату."),
                          "rows": [bk(back)]}
    order = [i for i in (ids["cities"], ids["categories"], ids["listing"], ids["districts"], ids["variants"], ids["pay"], ids["receipt"], ids["paid"], ids["delivered"]) if i]
    layout = {pid: [i * 320, 0] for i, pid in enumerate(order)}
    return pages, layout, first


# The template ships with another shop's artwork. New bots get neutral placeholders of the same
# names and sizes (the bot code still finds its files) — swap them for real pictures in the builder.
_NEUTRAL_ART = r"""
import sys, os
from PIL import Image, ImageDraw
d = sys.argv[1]
for f in sorted(os.listdir(d)):
    if not f.lower().endswith((".jpg", ".jpeg", ".png")):
        continue
    p = os.path.join(d, f)
    if f == "hades_logo.png":        # branding overlaid on QR codes — QR codes work without it
        os.remove(p)
        continue
    try:
        w, h = Image.open(p).size
    except Exception:
        continue
    img = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(img)
    for y in range(h):
        c = int(28 + 40 * y / max(h, 1))
        px.line([(0, y), (w, y)], fill=(c, c + 4, c + 22))
    label = os.path.splitext(f)[0].replace("_", " ")
    px.text((w // 2, h // 2), label, fill=(160, 160, 190), anchor="mm")
    img.save(p, "PNG" if f.lower().endswith(".png") else "JPEG")
"""

JOB_DIR = Path("/tmp/cx_jobs")   # shared between gunicorn workers


def _job_save(job: dict) -> None:
    JOB_DIR.mkdir(exist_ok=True)
    tmp = JOB_DIR / f"{job['id']}.tmp"
    tmp.write_text(json.dumps(job))
    tmp.replace(JOB_DIR / f"{job['id']}.json")


def _job_log(job: dict, msg: str) -> None:
    job["steps"].append({"t": time.strftime("%H:%M:%S"), "msg": msg})
    _job_save(job)


def _run_create(job: dict, p: dict) -> None:
    """Background worker: clone template, venv, .env, service, registry."""
    name = p["name"]
    bot_dir = Path("/opt") / name
    service = f"bot-{name}"
    try:
        _job_log(job, "Copying the bot template")
        remote = subprocess.run(["git", "-C", str(TEMPLATE_REPO), "remote", "get-url", "origin"],
                                capture_output=True, text=True).stdout.strip()
        public_remote = re.sub(r"//[^@/]*@", "//", remote)
        r = subprocess.run(["git", "clone", "--local", "--no-hardlinks", str(TEMPLATE_REPO), str(bot_dir)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError("clone failed: " + r.stderr[-300:])
        # include the template's latest working-tree files (uncommitted engine updates)
        subprocess.run(["rsync", "-a", "--exclude", ".git", "--exclude", "venv", "--exclude", "__pycache__",
                        "--exclude", ".env", "--exclude", "data.json", "--exclude", "bot-panel",
                        "--exclude", "photos_cache", "--exclude", "qr_cache",
                        f"{TEMPLATE_REPO}/", f"{bot_dir}/"], check=True, timeout=120)
        subprocess.run(["rm", "-rf", str(bot_dir / "bot-panel")], timeout=30)
        if public_remote:
            subprocess.run(["git", "-C", str(bot_dir), "remote", "set-url", "origin", public_remote], timeout=20)
        (bot_dir / "data.json").write_text(json.dumps({
            "products": {}, "districts": {}, "users": {}, "announcements_scheduled": [],
            "stats": {"total_orders": 0, "total_revenue_usd": 0.0}}, indent=2))

        _job_log(job, "Writing your menu")
        cfg = starter_config(p["template"], p["langs"])
        if p.get("reply_menu"):
            cfg["reply_menu"] = default_reply_menu(p["langs"], cfg["start_page"])
        (bot_dir / "menu_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

        _job_log(job, "Writing settings (.env)")
        env = {"BOT_TOKENS": ",".join(p["tokens"]), "ADMIN_IDS": ",".join(p["admins"]),
               "BTC_WALLET": "", "LTC_WALLET": "", "USDT_WALLET": ""}
        _write_env(bot_dir, env)

        _job_log(job, "Installing Python dependencies (about a minute)")
        subprocess.run(["python3", "-m", "venv", str(bot_dir / "venv")], check=True, timeout=120)
        subprocess.run([str(bot_dir / "venv/bin/pip"), "install", "-q", "-r", str(bot_dir / "requirements.txt")],
                       check=True, timeout=600)

        _job_log(job, "Replacing template artwork with neutral placeholders")
        subprocess.run([str(bot_dir / "venv/bin/python"), "-I", "-c", _NEUTRAL_ART, str(bot_dir)], timeout=120)

        _job_log(job, "Creating the service")
        Path(f"/etc/systemd/system/{service}.service").write_text(f"""[Unit]
Description=Telegram Bot - {p['display']}
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={bot_dir}
ExecStart={bot_dir}/venv/bin/python bot_engine.py
Restart=always
RestartSec=5
EnvironmentFile={bot_dir}/.env

[Install]
WantedBy=multi-user.target
""")
        subprocess.run(["systemctl", "daemon-reload"], timeout=20)
        if p["tokens"]:   # a token-less bot would crash-loop, so it is enabled once it has a token
            subprocess.run(["systemctl", "enable", service], timeout=20, capture_output=True)

        _job_log(job, "Registering in the panel (Global Admin picks it up automatically)")
        cfg_all = _load_bots()
        cfg_all.setdefault("bots", {})[name] = {
            "dir": str(bot_dir), "service": service, "display_name": p["display"],
            "repo": public_remote, "engine": True,
        }
        PANEL_CONFIG.write_text(json.dumps(cfg_all, indent=2, ensure_ascii=False))

        if p["tokens"] and p.get("start"):
            _job_log(job, "Starting the bot")
            subprocess.run(["systemctl", "start", service], timeout=30)
        job["status"] = "done"
        job["name"] = name
        _job_log(job, "Done")
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)[:500]
        _job_log(job, f"Failed: {job['error']}")
        # leave nothing half-built
        if bot_dir.exists() and name not in _load_bots().get("bots", {}):
            subprocess.run(["rm", "-rf", str(bot_dir)], timeout=60)


@constructor_bp.route("/constructor/new")
@login_required
def constructor_new():
    return render_template("constructor_new.html")


@constructor_bp.route("/constructor/api/create", methods=["POST"])
@login_required
def create_bot():
    d = request.get_json(force=True, silent=True) or {}
    name = str(d.get("name", "")).strip()
    if not _NAME_RE.match(name):
        return jsonify({"error": "Name: 2–39 characters, letters, digits, - or _"}), 400
    if name in _load_bots().get("bots", {}) or (Path("/opt") / name).exists() \
            or Path(f"/etc/systemd/system/bot-{name}.service").exists():
        return jsonify({"error": f"'{name}' already exists — pick another name"}), 409
    template = d.get("template") if d.get("template") in ("shop", "shop_categories", "blank") else "shop"
    langs = [l for l in d.get("langs", ["en"]) if l in ("en", "ka", "ru")] or ["en"]
    tokens = []
    for t in d.get("tokens", []):
        t = str(t).strip()
        if t:
            if not _telegram_me(t):
                return jsonify({"error": "A token was rejected by Telegram — check it or leave it empty for now"}), 400
            tokens.append(t)
    admins = [str(a).strip() for a in d.get("admins", []) if str(a).strip().isdigit()]
    p = {"name": name, "display": str(d.get("display_name", "")).strip()[:60] or name,
         "template": template, "langs": langs, "tokens": tokens, "admins": admins,
         "reply_menu": bool(d.get("reply_menu")), "start": bool(tokens)}
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "status": "running", "steps": [], "error": ""}
    _job_save(job)
    threading.Thread(target=_run_create, args=(job, p), daemon=True).start()
    return jsonify({"job": job_id})


@constructor_bp.route("/constructor/api/job/<job_id>")
@login_required
def create_job(job_id):
    if not re.match(r"^[0-9a-f]{12}$", job_id) or not (JOB_DIR / f"{job_id}.json").exists():
        return jsonify({"error": "unknown job"}), 404
    return jsonify(json.loads((JOB_DIR / f"{job_id}.json").read_text()))
