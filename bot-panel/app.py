"""
Bot Management Panel — lightweight Flask dashboard for managing
multiple Telegram bot instances deployed as systemd services.
"""

import os
import json
import subprocess
import secrets
import shutil
import time
import psutil
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, Response, session
)

app = Flask(__name__)
app.secret_key = os.environ.get("PANEL_SECRET", secrets.token_hex(32))

# ── Config ──────────────────────────────────────────────────
BOTS_BASE_DIR = Path(os.environ.get("BOTS_DIR", "/opt"))
PANEL_USER = os.environ.get("PANEL_USER", "admin")
PANEL_PASS = os.environ.get("PANEL_PASS", "changeme")
PANEL_CONFIG = Path("/etc/bot-panel/bots.json")
BACKUP_DIR = Path("/var/backups/bot-panel")

EDITABLE_EXTENSIONS = {
    ".py", ".json", ".txt", ".env", ".md", ".cfg",
    ".ini", ".yml", ".yaml", ".toml", ".html", ".css", ".js",
}


def load_bots_config() -> dict:
    if PANEL_CONFIG.exists():
        return json.loads(PANEL_CONFIG.read_text())
    return {"bots": {}}


def save_bots_config(config: dict):
    PANEL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    PANEL_CONFIG.write_text(json.dumps(config, indent=2))


# ── Auth ────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (request.form.get("username") == PANEL_USER and
                request.form.get("password") == PANEL_PASS):
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        flash("Invalid credentials", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))


# ── Helpers ─────────────────────────────────────────────────
def systemctl(action: str, service: str) -> tuple[int, str]:
    result = subprocess.run(
        ["systemctl", action, service],
        capture_output=True, text=True, timeout=15
    )
    return result.returncode, result.stdout + result.stderr


def get_service_status(service: str) -> dict:
    code, output = systemctl("is-active", service)
    is_active = output.strip()

    result = subprocess.run(
        ["systemctl", "show", service,
         "--property=ActiveEnterTimestamp,MainPID,MemoryCurrent"],
        capture_output=True, text=True, timeout=10
    )

    props = {}
    for line in result.stdout.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v

    return {
        "status": is_active,
        "pid": props.get("MainPID", ""),
        "since": props.get("ActiveEnterTimestamp", ""),
        "memory": props.get("MemoryCurrent", ""),
    }


def get_bot_files(bot_dir: Path) -> list[dict]:
    files = []
    for f in sorted(bot_dir.rglob("*")):
        if f.is_file() and f.suffix in EDITABLE_EXTENSIONS:
            rel = f.relative_to(bot_dir)
            parts = rel.parts
            if any(p in ("venv", "__pycache__", ".git", "node_modules") for p in parts):
                continue
            files.append({
                "path": str(rel),
                "name": f.name,
                "size": f.stat().st_size,
            })
    return files


def get_server_stats() -> dict:
    """System resource usage."""
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime_sec = time.time() - psutil.boot_time()
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    return {
        "cpu": cpu,
        "mem_used": round(mem.used / (1024**3), 1),
        "mem_total": round(mem.total / (1024**3), 1),
        "mem_pct": mem.percent,
        "disk_used": round(disk.used / (1024**3), 1),
        "disk_total": round(disk.total / (1024**3), 1),
        "disk_pct": disk.percent,
        "uptime": f"{days}d {hours}h",
    }


def backup_bot(bot_dir: Path, name: str) -> str:
    """Create a timestamped backup of a bot directory. Returns backup path."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"{name}-{ts}"
    shutil.copytree(
        bot_dir, backup_path,
        ignore=shutil.ignore_patterns("venv", "__pycache__", ".git")
    )
    return str(backup_path)


# ── Routes ──────────────────────────────────────────────────
@app.route("/")
@login_required
def dashboard():
    config = load_bots_config()
    bots = []
    for name, info in config.get("bots", {}).items():
        service = info.get("service", name)
        bot_dir = Path(info.get("dir", str(BOTS_BASE_DIR / name)))
        status = get_service_status(service)

        # Check if it's a git repo
        is_git = (bot_dir / ".git").exists()

        bots.append({
            "name": name,
            "display_name": info.get("display_name", name),
            "service": service,
            "dir": str(bot_dir),
            "status": status["status"],
            "pid": status["pid"],
            "since": status["since"],
            "memory": status["memory"],
            "is_git": is_git,
        })

    stats = get_server_stats()
    return render_template("dashboard.html", bots=bots, stats=stats)


# ── Bot actions ─────────────────────────────────────────────
@app.route("/bot/<name>/action/<action>", methods=["POST"])
@login_required
def bot_action(name, action):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))

    service = bot.get("service", name)
    if action not in ("start", "stop", "restart"):
        flash("Invalid action", "error")
        return redirect(url_for("dashboard"))

    code, output = systemctl(action, service)
    if code == 0:
        flash(f"Bot '{bot.get('display_name', name)}' — {action} OK", "success")
    else:
        flash(f"Failed: {output}", "error")
    return redirect(url_for("dashboard"))


# ── Bulk actions ────────────────────────────────────────────
@app.route("/bulk-action", methods=["POST"])
@login_required
def bulk_action():
    action = request.form.get("action")
    selected = request.form.getlist("selected_bots")
    if not selected:
        flash("No bots selected", "error")
        return redirect(url_for("dashboard"))

    config = load_bots_config()
    ok_count = 0
    for name in selected:
        bot = config["bots"].get(name)
        if bot:
            service = bot.get("service", name)
            code, _ = systemctl(action, service)
            if code == 0:
                ok_count += 1

    flash(f"{action.title()} — {ok_count}/{len(selected)} bots OK", "success")
    return redirect(url_for("dashboard"))


# ── .env editor ─────────────────────────────────────────────
@app.route("/bot/<name>/env", methods=["GET", "POST"])
@login_required
def bot_env(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))

    env_path = Path(bot["dir"]) / ".env"

    if request.method == "POST":
        content = request.form.get("content", "")
        env_path.write_text(content)
        flash("Saved .env — restart the bot to apply.", "success")
        return redirect(url_for("bot_env", name=name))

    content = env_path.read_text() if env_path.exists() else ""
    display = bot.get("display_name", name)
    return render_template("env_editor.html", name=name, display_name=display, content=content)


# ── File browser + editor ──────────────────────────────────
@app.route("/bot/<name>/files")
@login_required
def bot_files(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))

    bot_dir = Path(bot["dir"])
    files = get_bot_files(bot_dir)
    display = bot.get("display_name", name)
    return render_template("files.html", name=name, display_name=display, files=files)


@app.route("/bot/<name>/edit/<path:filepath>", methods=["GET", "POST"])
@login_required
def edit_file(name, filepath):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))

    bot_dir = Path(bot["dir"])
    file_path = bot_dir / filepath

    # Security: path must stay within bot_dir
    try:
        file_path.resolve().relative_to(bot_dir.resolve())
    except ValueError:
        flash("Invalid file path", "error")
        return redirect(url_for("bot_files", name=name))

    if request.method == "POST":
        content = request.form.get("content", "")
        file_path.write_text(content)
        flash(f"Saved {filepath}", "success")
        return redirect(url_for("edit_file", name=name, filepath=filepath))

    content = file_path.read_text() if file_path.exists() else ""
    return render_template("code_editor.html", name=name, filepath=filepath, content=content)


# ── Logs ────────────────────────────────────────────────────
@app.route("/bot/<name>/logs")
@login_required
def bot_logs(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))
    service = bot.get("service", name)
    display = bot.get("display_name", name)
    return render_template("logs.html", name=name, display_name=display, service=service)


@app.route("/api/bot/<name>/logs")
@login_required
def api_bot_logs(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        return jsonify({"error": "not found"}), 404

    service = bot.get("service", name)
    lines = request.args.get("lines", "200")
    result = subprocess.run(
        ["journalctl", "-u", service, "-n", lines, "--no-pager", "--output=short-iso"],
        capture_output=True, text=True, timeout=10
    )
    return jsonify({"logs": result.stdout})


@app.route("/bot/<name>/logs/stream")
@login_required
def stream_logs(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        return "Bot not found", 404

    service = bot.get("service", name)

    def generate():
        proc = subprocess.Popen(
            ["journalctl", "-u", service, "-f", "--no-pager", "--output=short-iso"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        try:
            for line in proc.stdout:
                yield f"data: {line}\n\n"
        finally:
            proc.kill()

    return Response(generate(), mimetype="text/event-stream")


# ── Rename ──────────────────────────────────────────────────
@app.route("/bot/<name>/rename", methods=["POST"])
@login_required
def rename_bot(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))

    new_display = request.form.get("new_name", "").strip()
    if not new_display:
        flash("Name cannot be empty", "error")
        return redirect(url_for("dashboard"))

    bot["display_name"] = new_display
    save_bots_config(config)
    flash(f"Renamed to '{new_display}'", "success")
    return redirect(url_for("dashboard"))


# ── Backup ──────────────────────────────────────────────────
@app.route("/bot/<name>/backup", methods=["POST"])
@login_required
def bot_backup(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))

    try:
        path = backup_bot(Path(bot["dir"]), name)
        flash(f"Backup created: {path}", "success")
    except Exception as e:
        flash(f"Backup failed: {e}", "error")
    return redirect(url_for("dashboard"))


# ── Git pull (update from repo) ─────────────────────────────
@app.route("/bot/<name>/git-pull", methods=["POST"])
@login_required
def bot_git_pull(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))

    bot_dir = Path(bot["dir"])
    if not (bot_dir / ".git").exists():
        flash("Not a git repository", "error")
        return redirect(url_for("dashboard"))

    # Backup first
    try:
        bk = backup_bot(bot_dir, name)
    except Exception:
        bk = None

    # Pull
    result = subprocess.run(
        ["git", "-C", str(bot_dir), "pull", "--force"],
        capture_output=True, text=True, timeout=60
    )

    if result.returncode == 0:
        # Reinstall deps
        venv_pip = bot_dir / "venv" / "bin" / "pip"
        req = bot_dir / "requirements.txt"
        if venv_pip.exists() and req.exists():
            subprocess.run([str(venv_pip), "install", "-r", str(req), "-q"], timeout=120)

        msg = f"Updated from git. "
        if bk:
            msg += f"Backup at {bk}. "
        msg += "Restart the bot to apply."
        flash(msg, "success")
    else:
        flash(f"Git pull failed: {result.stderr}", "error")

    return redirect(url_for("dashboard"))


# ── Add new bot ─────────────────────────────────────────────
@app.route("/add-bot", methods=["GET", "POST"])
@login_required
def add_bot():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        display_name = request.form.get("display_name", "").strip() or name
        repo = request.form.get("repo", "").strip()
        bot_tokens = request.form.get("bot_tokens", "").strip()
        admin_ids = request.form.get("admin_ids", "").strip()
        btc = request.form.get("btc_wallet", "").strip()
        ltc = request.form.get("ltc_wallet", "").strip()
        usdt = request.form.get("usdt_wallet", "").strip()

        if not name:
            flash("Bot name is required", "error")
            return redirect(url_for("add_bot"))

        bot_dir = BOTS_BASE_DIR / name
        service_name = f"bot-{name}"

        # Clone repo
        if repo:
            result = subprocess.run(
                ["git", "clone", repo, str(bot_dir)],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                flash(f"Git clone failed: {result.stderr}", "error")
                return redirect(url_for("add_bot"))

        # Setup venv
        if (bot_dir / "requirements.txt").exists():
            subprocess.run(["python3", "-m", "venv", str(bot_dir / "venv")], timeout=30)
            subprocess.run(
                [str(bot_dir / "venv/bin/pip"), "install", "-r",
                 str(bot_dir / "requirements.txt"), "-q"],
                timeout=120
            )

        # Write .env
        if bot_tokens:
            env_lines = [f"BOT_TOKENS={bot_tokens}"]
            if admin_ids:
                env_lines.append(f"ADMIN_IDS={admin_ids}")
            if btc:
                env_lines.append(f"BTC_WALLET={btc}")
            if ltc:
                env_lines.append(f"LTC_WALLET={ltc}")
            if usdt:
                env_lines.append(f"USDT_WALLET={usdt}")
            (bot_dir / ".env").write_text("\n".join(env_lines) + "\n")
            os.chmod(bot_dir / ".env", 0o600)

        # Create systemd service
        service_content = f"""[Unit]
Description=Telegram Bot - {display_name}
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={bot_dir}
ExecStart={bot_dir}/venv/bin/python bot.py
Restart=always
RestartSec=5
EnvironmentFile={bot_dir}/.env

[Install]
WantedBy=multi-user.target
"""
        Path(f"/etc/systemd/system/{service_name}.service").write_text(service_content)
        subprocess.run(["systemctl", "daemon-reload"], timeout=10)
        subprocess.run(["systemctl", "enable", service_name], timeout=10)

        # Register
        config = load_bots_config()
        config["bots"][name] = {
            "dir": str(bot_dir),
            "service": service_name,
            "display_name": display_name,
            "repo": repo,
        }
        save_bots_config(config)

        flash(f"Bot '{display_name}' deployed! Start it from the dashboard.", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_bot.html")


# ── Remove bot ──────────────────────────────────────────────
@app.route("/bot/<name>/remove", methods=["POST"])
@login_required
def remove_bot(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))

    service = bot.get("service", name)
    systemctl("stop", service)
    systemctl("disable", service)
    service_file = Path(f"/etc/systemd/system/{service}.service")
    if service_file.exists():
        service_file.unlink()
    subprocess.run(["systemctl", "daemon-reload"], timeout=10)

    if request.form.get("delete_files") == "yes":
        bot_dir = Path(bot["dir"])
        if bot_dir.exists():
            shutil.rmtree(bot_dir)

    del config["bots"][name]
    save_bots_config(config)
    flash(f"Bot '{bot.get('display_name', name)}' removed.", "success")
    return redirect(url_for("dashboard"))


# ── Attach existing bot ─────────────────────────────────────
@app.route("/attach-bot", methods=["GET", "POST"])
@login_required
def attach_bot():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        display_name = request.form.get("display_name", "").strip() or name
        directory = request.form.get("directory", "").strip()
        service = request.form.get("service", "").strip()

        if not all([name, directory, service]):
            flash("All fields are required", "error")
            return redirect(url_for("attach_bot"))

        if not Path(directory).exists():
            flash(f"Directory {directory} does not exist", "error")
            return redirect(url_for("attach_bot"))

        config = load_bots_config()
        config["bots"][name] = {
            "dir": directory,
            "service": service,
            "display_name": display_name,
        }
        save_bots_config(config)
        flash(f"Bot '{display_name}' attached!", "success")
        return redirect(url_for("dashboard"))

    return render_template("attach_bot.html")


# ── API: status for auto-refresh ────────────────────────────
@app.route("/api/status")
@login_required
def api_status():
    config = load_bots_config()
    result = {}
    for name, info in config.get("bots", {}).items():
        service = info.get("service", name)
        status = get_service_status(service)
        result[name] = status
    return jsonify(result)


@app.route("/api/server-stats")
@login_required
def api_server_stats():
    return jsonify(get_server_stats())


# ── Backups list ────────────────────────────────────────────
@app.route("/backups")
@login_required
def backups_list():
    backups = []
    if BACKUP_DIR.exists():
        for d in sorted(BACKUP_DIR.iterdir(), reverse=True):
            if d.is_dir():
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                backups.append({
                    "name": d.name,
                    "path": str(d),
                    "size": round(size / (1024 * 1024), 1),
                    "created": datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                })
    return render_template("backups.html", backups=backups)


@app.route("/backup/<name>/delete", methods=["POST"])
@login_required
def delete_backup(name):
    path = BACKUP_DIR / name
    if path.exists() and path.is_dir():
        shutil.rmtree(path)
        flash(f"Backup '{name}' deleted.", "success")
    else:
        flash("Backup not found", "error")
    return redirect(url_for("backups_list"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
