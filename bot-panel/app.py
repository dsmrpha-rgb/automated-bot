"""
Bot Management Panel — lightweight Flask dashboard for managing
multiple Telegram bot instances deployed as systemd services.
"""

import os
import json
import subprocess
import secrets
import shutil
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

EDITABLE_EXTENSIONS = {".py", ".json", ".txt", ".env", ".md", ".cfg", ".ini", ".yml", ".yaml", ".toml"}


def load_bots_config() -> dict:
    """Load registered bots from config file."""
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
    """Run a systemctl command, return (returncode, output)."""
    result = subprocess.run(
        ["systemctl", action, service],
        capture_output=True, text=True, timeout=15
    )
    return result.returncode, result.stdout + result.stderr


def get_service_status(service: str) -> dict:
    """Get systemd service status as a dict."""
    code, output = systemctl("is-active", service)
    is_active = output.strip()

    code2, output2 = subprocess.run(
        ["systemctl", "show", service,
         "--property=ActiveEnterTimestamp,MainPID,MemoryCurrent"],
        capture_output=True, text=True, timeout=10
    ).returncode, subprocess.run(
        ["systemctl", "show", service,
         "--property=ActiveEnterTimestamp,MainPID,MemoryCurrent"],
        capture_output=True, text=True, timeout=10
    ).stdout

    props = {}
    for line in output2.strip().split("\n"):
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
    """List editable files in a bot directory."""
    files = []
    for f in sorted(bot_dir.rglob("*")):
        if f.is_file() and f.suffix in EDITABLE_EXTENSIONS:
            rel = f.relative_to(bot_dir)
            # Skip venv and __pycache__
            parts = rel.parts
            if any(p in ("venv", "__pycache__", ".git", "node_modules") for p in parts):
                continue
            files.append({
                "path": str(rel),
                "name": f.name,
                "size": f.stat().st_size,
            })
    return files


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
        bots.append({
            "name": name,
            "service": service,
            "dir": str(bot_dir),
            "status": status["status"],
            "pid": status["pid"],
            "since": status["since"],
            "memory": status["memory"],
        })
    return render_template("dashboard.html", bots=bots)


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
        flash(f"Bot '{name}' — {action} OK", "success")
    else:
        flash(f"Bot '{name}' — {action} failed: {output}", "error")
    return redirect(url_for("dashboard"))


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
        flash("Saved .env — restart the bot to apply changes.", "success")
        return redirect(url_for("bot_env", name=name))

    content = env_path.read_text() if env_path.exists() else ""
    return render_template("env_editor.html", name=name, content=content)


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
    return render_template("files.html", name=name, files=files)


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

    # Security: ensure path stays within bot_dir
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
    return render_template("code_editor.html", name=name,
                           filepath=filepath, content=content)


@app.route("/bot/<name>/logs")
@login_required
def bot_logs(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))
    service = bot.get("service", name)
    return render_template("logs.html", name=name, service=service)


@app.route("/api/bot/<name>/logs")
@login_required
def api_bot_logs(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        return jsonify({"error": "not found"}), 404

    service = bot.get("service", name)
    lines = request.args.get("lines", "100")
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


# ── Add / Remove bots ──────────────────────────────────────
@app.route("/add-bot", methods=["GET", "POST"])
@login_required
def add_bot():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        repo = request.form.get("repo", "").strip()
        bot_tokens = request.form.get("bot_tokens", "").strip()
        admin_ids = request.form.get("admin_ids", "").strip()

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
            subprocess.run(["python3", "-m", "venv", str(bot_dir / "venv")],
                           timeout=30)
            subprocess.run(
                [str(bot_dir / "venv/bin/pip"), "install", "-r",
                 str(bot_dir / "requirements.txt"), "-q"],
                timeout=120
            )

        # Write .env
        if bot_tokens:
            env_content = f"BOT_TOKENS={bot_tokens}\n"
            if admin_ids:
                env_content += f"ADMIN_IDS={admin_ids}\n"
            (bot_dir / ".env").write_text(env_content)
            os.chmod(bot_dir / ".env", 0o600)

        # Create systemd service
        service_content = f"""[Unit]
Description=Telegram Bot - {name}
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

        # Register in panel config
        config = load_bots_config()
        config["bots"][name] = {
            "dir": str(bot_dir),
            "service": service_name,
            "repo": repo,
        }
        save_bots_config(config)

        flash(f"Bot '{name}' added! Start it from the dashboard.", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_bot.html")


@app.route("/bot/<name>/remove", methods=["POST"])
@login_required
def remove_bot(name):
    config = load_bots_config()
    bot = config["bots"].get(name)
    if not bot:
        flash(f"Bot '{name}' not found", "error")
        return redirect(url_for("dashboard"))

    service = bot.get("service", name)

    # Stop and disable service
    systemctl("stop", service)
    systemctl("disable", service)
    service_file = Path(f"/etc/systemd/system/{service}.service")
    if service_file.exists():
        service_file.unlink()
    subprocess.run(["systemctl", "daemon-reload"], timeout=10)

    # Optionally remove files
    if request.form.get("delete_files") == "yes":
        bot_dir = Path(bot["dir"])
        if bot_dir.exists():
            shutil.rmtree(bot_dir)

    # Remove from config
    del config["bots"][name]
    save_bots_config(config)

    flash(f"Bot '{name}' removed.", "success")
    return redirect(url_for("dashboard"))


# ── Attach existing bot ─────────────────────────────────────
@app.route("/attach-bot", methods=["GET", "POST"])
@login_required
def attach_bot():
    """Register an already-deployed bot (existing dir + systemd service)."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
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
        }
        save_bots_config(config)

        flash(f"Bot '{name}' attached!", "success")
        return redirect(url_for("dashboard"))

    return render_template("attach_bot.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
