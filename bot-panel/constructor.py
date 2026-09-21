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

constructor_bp = Blueprint("constructor", __name__)

PANEL_CONFIG = Path(os.environ.get("PANEL_CONFIG", "/etc/bot-panel/bots.json"))
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_CONFIG_BYTES = 2 * 1024 * 1024

_SLUG_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_ACTION_RE = re.compile(
    r"^("
    r"page:[a-z0-9_]{1,40}"
    r"|builtin:[a-z0-9_]{1,30}"
    r"|url:https?://\S{1,300}"
    r"|choose:[a-zA-Z0-9_]{1,30}:[a-zA-Z0-9_\-.]{1,40}:[a-z0-9_]{1,40}"
    r"|confirm:[a-z0-9_]{1,40}"
    r"|noop"
    r")$"
)
_PAGE_TYPES = {"message", "input", "receipt", "list"}
_STYLES = {"green", "red", "check", "cross"}
_SOURCES = {"products", "districts"}


def _action_target_page(action: str) -> str | None:
    """Return the page id an action navigates to, if any."""
    if action.startswith("page:"):
        return action.split(":", 1)[1]
    if action.startswith("confirm:"):
        return action.split(":", 1)[1]
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
                clean_btn = {"label": label, "action": action}
                if btn.get("style") in _STYLES:
                    clean_btn["style"] = btn["style"]
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

        # optional item lookup on any page (merges product/district fields into vars)
        il = page.get("item_lookup")
        if isinstance(il, dict) and il.get("source"):
            clean_page["item_lookup"] = {
                "source": il["source"] if il["source"] in _SOURCES else "products",
                "city": re.sub(r"[^a-zA-Z0-9_]", "", str(il.get("city", "tbilisi")))[:30] or "tbilisi",
                "var": re.sub(r"[^a-zA-Z0-9_]", "", str(il.get("var", "product")))[:30] or "product",
            }

        clean_pages[pid] = clean_page

    return True, "", {
        "version": 1,
        "start_page": start,
        "default_lang": default_lang,
        "langs": langs,
        "pages": clean_pages,
    }


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
                imgs.append({"name": f.name, "size": f.stat().st_size})
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
    if len(blob) > MAX_IMAGE_BYTES:
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


def _parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@constructor_bp.route("/bot/<name>/api/env", methods=["GET"])
@login_required
def get_env(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    env_path = _bot_dir(bot) / ".env"
    env = _parse_env(env_path.read_text()) if env_path.exists() else {}
    tokens = [t for t in env.get("BOT_TOKENS", env.get("BOT_TOKEN", "")).split(",") if t.strip()]
    admins = [a for a in env.get("ADMIN_IDS", "").split(",") if a.strip()]
    return jsonify({
        "tokens": tokens,
        "admins": admins,
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
    existing = _parse_env(env_path.read_text()) if env_path.exists() else {}

    existing["BOT_TOKENS"] = ",".join(tokens)
    existing["ADMIN_IDS"] = ",".join(admins)
    existing["BTC_WALLET"] = str(data.get("btc", "")).strip()
    existing["LTC_WALLET"] = str(data.get("ltc", "")).strip()
    existing["USDT_WALLET"] = str(data.get("usdt", "")).strip()

    # rebuild: known keys first, then any others preserved
    lines = [f"{k}={existing[k]}" for k in _ENV_KNOWN if k in existing]
    for k, v in existing.items():
        if k not in _ENV_KNOWN and k != "BOT_TOKEN":
            lines.append(f"{k}={v}")
    env_path.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(env_path, 0o600)
    except Exception:
        pass
    return jsonify({"ok": True})


@constructor_bp.route("/bot/<name>/api/restart", methods=["POST"])
@login_required
def restart_bot(name):
    bot = _bot_or_none(name)
    if not bot:
        return jsonify({"error": "not found"}), 404
    service = bot.get("service", name)
    r = subprocess.run(["systemctl", "restart", service],
                       capture_output=True, text=True, timeout=20)
    if r.returncode == 0:
        return jsonify({"ok": True})
    return jsonify({"error": r.stderr or "restart failed"}), 500
