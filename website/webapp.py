"""CAPONE.CC storefront: the Telegram shop's listings as a website with accounts."""
import os
import secrets
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from flask import (Flask, abort, flash, redirect, render_template, request, send_file, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

import site_store as S

BASE = Path(__file__).parent
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
_sk = BASE / ".secret"
if not _sk.exists():
    _sk.write_text(secrets.token_hex(32))
    _sk.chmod(0o600)
app.secret_key = _sk.read_text().strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("SITE_SECURE") == "1", PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14)


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def guard():
    if not S.site_settings().get("enabled", True) and request.endpoint not in ("static",):
        return render_template("closed.html"), 503
    if request.method == "POST" and not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "x")):
        abort(400)


def current_user():
    login = session.get("user")
    u = S.get_user(login) if login else None
    if login and not u:
        session.pop("user", None)
    return u


def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not current_user():
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return f(*a, **kw)
    return w


def safe_next(target: str) -> str:
    return target if target and target.startswith("/") and not target.startswith("//") else url_for("index")


@app.context_processor
def inject():
    return dict(site=S.site_settings(), me=current_user(), csrf=csrf_token(), money=S.money, VARIANTS=S.VARIANTS,
                media=lambda n: url_for("media", name=n) if n else "")


def product_image(p: dict, cat: dict | None = None) -> str:
    for n in (p.get("image"), (cat or {}).get("image")):
        if S.media_file(n or ""):
            return url_for("media", name=n)
    d = S.site_settings().get("default_image")
    return url_for("media", name=d) if S.media_file(d or "") else url_for("static", filename="gift.svg")


app.jinja_env.globals["product_image"] = product_image


def crumbs(city=None, cat=None, product=None, variant=None):
    out = []
    if city:
        out.append(("You Choose City-აირჩიეთ ქალაქი", city.capitalize()))
    if cat:
        out.append(("You Choose Category - აირჩიეთ კატეგორია", cat))
    if product:
        out.append(("You Choose Product - აირჩიეთ პროდუქტი", product))
    if variant:
        out.append(("Вариант", variant))
    return out


# ── catalogue ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    cs = S.settings_cities()
    return render_template("index.html", cities=cs)


@app.route("/city/<city>")
def city_page(city):
    if city not in S.cities():
        abort(404)
    cats = S.city_categories(city)
    if not cats:
        return redirect(url_for("products", city=city, cat="-"))
    return render_template("categories.html", city=city, cats=cats)


@app.route("/city/<city>/<cat>")
def products(city, cat):
    if city not in S.cities():
        abort(404)
    if cat == "-":
        items, cname, cobj = S.city_products(city), None, None
    else:
        cobj = S.city_categories(city).get(cat)
        if not cobj:
            abort(404)
        items, cname = S.category_products(city, cat), cobj.get("name")
    coin = S.site_settings().get("coin", "LTC")
    rows = [(k, p, S.crypto_amount(p.get("price_usd", 0), coin)) for k, p in items.items() if p.get("districts")]
    page = request.args.get("rp", 1, type=int)
    revs, pages, total = S.reviews(page)
    return render_template("products.html", city=city, cat=cat, cobj=cobj, rows=rows, crumb=crumbs(city, cname),
                           revs=revs, pages=pages, page=page, total=total)


def _load_offer(slug):
    p = S.ds.get_product(slug)
    if not p or p.get("owner") or not p.get("districts"):
        abort(404)
    return p


@app.route("/p/<path:slug>/variants")
def variants(slug):
    p = _load_offer(slug)
    vs = S.ds.product_variants(p)
    if len(vs) == 1:
        return redirect(url_for("districts", slug=slug, variant=vs[0]))
    cat = S.ds.get_categories().get(p.get("category", ""), {})
    return render_template("variants.html", slug=slug, p=p, vs=vs, cat=cat,
                           crumb=crumbs(p.get("city"), cat.get("name"), p.get("name")))


@app.route("/p/<path:slug>/<variant>/districts")
def districts(slug, variant):
    p = _load_offer(slug)
    if variant not in S.ds.product_variants(p):
        abort(404)
    cat = S.ds.get_categories().get(p.get("category", ""), {})
    return render_template("districts.html", slug=slug, p=p, variant=variant, ds_=S.product_districts(slug),
                           crumb=crumbs(p.get("city"), cat.get("name"), p.get("name"), S.VARIANTS[variant]))


@app.route("/checkout")
def checkout():
    slug, variant, did = request.args.get("p", ""), request.args.get("v", ""), request.args.get("d", "")
    p = _load_offer(slug)
    d = S.product_districts(slug).get(did)
    if not d or variant not in S.ds.product_variants(p):
        abort(404)
    price = float(p.get("price_usd", 0) or 0)
    me = current_user()
    return render_template("checkout.html", slug=slug, p=p, variant=variant, did=did, d=d, price=price,
                           crypto=S.crypto_amount(price, S.site_settings().get("coin", "LTC")),
                           enough=bool(me and float(me.get("balance", 0)) + 1e-9 >= price),
                           soldout=p.get("quantity", -1) == 0, img=product_image(p, S.ds.get_categories().get(p.get("category", ""))))


@app.route("/buy", methods=["POST"])
@login_required
def buy():
    slug, variant, did = request.form.get("p", ""), request.form.get("v", ""), request.form.get("d", "")
    ok, msg, rec = S.buy(session["user"], slug, variant, did)
    if not ok:
        flash(msg, "err")
        return redirect(url_for("checkout", p=slug, v=variant, d=did))
    S.notify_admins("purchase", f"🛒 Website: {rec['web_user']} bought “{rec['product']}” for {S.money(rec['price_usd'])}.\n{rec['note']}")
    return redirect(url_for("order", pid=rec["id"]))


# ── accounts ─────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        key = request.remote_addr or "?"
        if S.too_many_attempts(key):
            flash("Too many attempts, try again later · ცადეთ მოგვიანებით", "err")
        else:
            u, err = S.authenticate(request.form.get("login", ""), request.form.get("password", ""))
            if u:
                session.clear()
                session["user"] = u["login"]
                session.permanent = True
                return redirect(safe_next(request.args.get("next", "")))
            S.note_attempt(key)
            flash(err, "err")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        key = "reg:" + (request.remote_addr or "?")
        if S.too_many_attempts(key):
            flash("Too many attempts, try again later · ცადეთ მოგვიანებით", "err")
        else:
            S.note_attempt(key)
            ok, res = S.register(request.form.get("login", ""), request.form.get("password", ""), request.form.get("password2", ""))
            if ok:
                session.clear()
                session["user"] = res
                session.permanent = True
                S.notify_admins("site_user", f"🆕 New website user: {res}")
                return redirect(safe_next(request.args.get("next", "")) if request.args.get("next") else url_for("account"))
            flash(res, "err")
    return render_template("register.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/account")
@login_required
def account():
    me = current_user()
    return render_template("account.html", orders=S.user_orders(me["login"]), wallets=S.wallets(), coin=S.site_settings().get("coin"))


@app.route("/account/topup", methods=["POST"])
@login_required
def topup():
    me = current_user()
    coin = request.form.get("coin", "")[:10]
    txid = request.form.get("txid", "").strip()[:100]
    S.notify_admins("topup_started", f"💳 Website top-up request: {me['login']} says they sent {coin}"
                    + (f" (tx {txid})" if txid else "") + ". Check the wallet and credit the balance in Global Admin → Website users.")
    flash("Thanks! An admin will credit your balance after the payment is confirmed · ბალანსი შეივსება გადახდის დადასტურების შემდეგ", "ok")
    return redirect(url_for("account"))


@app.route("/order/<int:pid>")
@login_required
def order(pid):
    o = S.get_order(pid, session["user"])
    if not o:
        abort(404)
    return render_template("order.html", o=o, dv=S.delivery_for(o))


@app.route("/order/<int:pid>/review", methods=["POST"])
@login_required
def review(pid):
    ok, msg = S.add_review(session["user"], pid, request.form.get("text", ""))
    flash(msg, "ok" if ok else "err")
    return redirect(url_for("order", pid=pid))


@app.route("/order/<int:pid>/file")
@login_required
def order_file(pid):
    o = S.get_order(pid, session["user"])
    f = S.media_file(S.delivery_for(o).get("image", "")) if o else None
    if not f:
        abort(404)
    return send_file(f)


@app.route("/media/<name>")
def media(name):
    f = S.media_file(name)
    if not f:
        abort(404)
    resp = send_file(f, max_age=3600)
    return resp


@app.route("/healthz")
def healthz():
    return "ok"
