"""File manager for the storefront (/opt/bot-site) and the nes666.tech web roots, inside the panel."""
import mimetypes
import shutil
import subprocess
import time
from functools import wraps
from pathlib import Path

from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_file, session, url_for

files_bp = Blueprint("site_files", __name__)

ROOTS = {
    "storefront": {"label": "Storefront (CAPONE site)", "path": Path("/opt/bot-site"), "service": "bot-site"},
    "nes666": {"label": "nes666.tech", "path": Path("/var/www/html"), "service": None},
    "nes666-www": {"label": "www.nes666.tech", "path": Path("/var/www/mywebsite"), "service": None},
}
HIDDEN = {"venv", "__pycache__", ".git", "node_modules"}
PROTECTED = {".secret"}
TEXT_EXTS = {".py", ".html", ".htm", ".css", ".js", ".json", ".txt", ".md", ".svg", ".xml", ".cfg", ".ini",
             ".yml", ".yaml", ".toml", ".conf", ".map", ".csv", ".webmanifest"}
MAX_EDIT = 1024 * 1024
BACKUP_DIR = Path("/var/backups/site-files")


def api(f):
    @wraps(f)
    def wrapper(*a, **k):
        if not session.get("logged_in"):
            return jsonify(error="login required"), 401
        if request.method != "GET" and request.headers.get("X-Requested-With") != "site-files":
            return jsonify(error="bad request"), 400
        try:
            return f(*a, **k)
        except PermissionError as e:
            return jsonify(error=str(e) or "forbidden"), 403
        except FileNotFoundError:
            return jsonify(error="not found"), 404
        except OSError as e:
            return jsonify(error=str(e)), 400
    return wrapper


def resolve(root: str, rel: str, allow_root=True) -> Path:
    if root not in ROOTS:
        abort(404)
    base = ROOTS[root]["path"].resolve()
    p = (base / (rel or "").lstrip("/")).resolve()
    if p != base and base not in p.parents:
        raise PermissionError("path outside the site folder")
    rp = p.relative_to(base).parts
    if any(x in HIDDEN or x in PROTECTED for x in rp):
        raise PermissionError("this path is protected")
    if p == base and not allow_root:
        raise PermissionError("cannot modify the site root")
    return p


def backup(root: str, p: Path):
    if p.is_file():
        d = BACKUP_DIR / root
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, d / f"{time.strftime('%Y%m%d-%H%M%S')}-{p.name}")


@files_bp.route("/sitefiles")
def page():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template("site_files.html", roots={k: v["label"] for k, v in ROOTS.items()})


@files_bp.route("/sitefiles/api/<root>/list")
@api
def ls(root):
    p = resolve(root, request.args.get("path", ""))
    if not p.is_dir():
        raise FileNotFoundError
    items = []
    for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if c.name in HIDDEN or c.name in PROTECTED:
            continue
        st = c.stat()
        items.append({"name": c.name, "dir": c.is_dir(), "size": st.st_size, "mtime": int(st.st_mtime),
                      "text": c.suffix.lower() in TEXT_EXTS, "image": c.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}})
    return jsonify(items=items, service=ROOTS[root]["service"])


@files_bp.route("/sitefiles/api/<root>/read")
@api
def read(root):
    p = resolve(root, request.args.get("path", ""))
    if not p.is_file():
        raise FileNotFoundError
    if p.suffix.lower() not in TEXT_EXTS and p.suffix:
        raise PermissionError("not a text file; download it instead")
    if p.stat().st_size > MAX_EDIT:
        raise PermissionError("file too large to edit here (limit 1 MB)")
    return jsonify(content=p.read_text(encoding="utf-8", errors="replace"))


@files_bp.route("/sitefiles/api/<root>/write", methods=["POST"])
@api
def write(root):
    d = request.get_json(silent=True) or {}
    p = resolve(root, d.get("path", ""), allow_root=False)
    if p.is_dir():
        raise PermissionError("is a directory")
    backup(root, p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(d.get("content", ""), encoding="utf-8")
    return jsonify(ok=True)


@files_bp.route("/sitefiles/api/<root>/mkdir", methods=["POST"])
@api
def mkdir(root):
    d = request.get_json(silent=True) or {}
    resolve(root, d.get("path", ""), allow_root=False).mkdir(parents=True, exist_ok=True)
    return jsonify(ok=True)


@files_bp.route("/sitefiles/api/<root>/delete", methods=["POST"])
@api
def delete(root):
    d = request.get_json(silent=True) or {}
    p = resolve(root, d.get("path", ""), allow_root=False)
    if p.is_dir():
        shutil.rmtree(p)
    else:
        backup(root, p)
        p.unlink()
    return jsonify(ok=True)


@files_bp.route("/sitefiles/api/<root>/rename", methods=["POST"])
@api
def rename(root):
    d = request.get_json(silent=True) or {}
    src = resolve(root, d.get("path", ""), allow_root=False)
    dst = resolve(root, d.get("to", ""), allow_root=False)
    if dst.exists():
        raise PermissionError("target already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return jsonify(ok=True)


@files_bp.route("/sitefiles/api/<root>/upload", methods=["POST"])
@api
def upload(root):
    folder = resolve(root, request.form.get("path", ""))
    n = 0
    for f in request.files.getlist("files"):
        name = Path(f.filename or "").name
        if not name or name in PROTECTED or name in HIDDEN:
            continue
        dst = resolve(root, str((folder / name).relative_to(ROOTS[root]["path"].resolve())), allow_root=False)
        backup(root, dst)
        f.save(dst)
        n += 1
    return jsonify(ok=True, uploaded=n)


@files_bp.route("/sitefiles/api/<root>/raw")
def raw(root):
    if not session.get("logged_in"):
        abort(401)
    try:
        p = resolve(root, request.args.get("path", ""))
    except PermissionError:
        abort(403)
    if not p.is_file():
        abort(404)
    dl = request.args.get("dl") == "1"
    mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if not dl and mt not in ("image/png", "image/jpeg", "image/gif", "image/webp", "image/x-icon", "image/vnd.microsoft.icon"):
        mt, dl = "application/octet-stream", True
    return send_file(p, mimetype=mt, as_attachment=dl, download_name=p.name)


@files_bp.route("/sitefiles/api/<root>/restart", methods=["POST"])
@api
def restart(root):
    svc = ROOTS.get(root, {}).get("service")
    if not svc:
        raise PermissionError("nothing to restart for this folder")
    r = subprocess.run(["systemctl", "restart", svc], capture_output=True, text=True, timeout=60)
    return jsonify(ok=r.returncode == 0, error=r.stderr.strip())
