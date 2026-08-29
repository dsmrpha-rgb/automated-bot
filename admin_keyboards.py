"""Inline keyboards for the admin panel."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


# ── Main admin menu ────────────────────────────────────────────────────

def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 პროდუქტები", callback_data="admin:products"),
         InlineKeyboardButton(text="🗺 უბნები", callback_data="admin:districts")],
        [InlineKeyboardButton(text="📢 განცხადება", callback_data="admin:broadcast"),
         InlineKeyboardButton(text="📅 დაგეგმილი", callback_data="admin:scheduled")],
        [InlineKeyboardButton(text="👥 მომხმარებლები", callback_data="admin:users"),
         InlineKeyboardButton(text="📊 სტატისტიკა", callback_data="admin:stats")],
        [InlineKeyboardButton(text="◀ მთავარი მენიუ", callback_data="menu:main")],
    ])


# ── Product management ─────────────────────────────────────────────────

def products_menu_kb(products: dict) -> InlineKeyboardMarkup:
    """List all products with delete buttons + add button."""
    rows = []
    for slug, info in products.items():
        name = info["name"]
        price = info["price_usd"]
        qty = info["quantity"]
        qty_text = "∞" if qty == -1 else str(qty)
        rows.append([
            InlineKeyboardButton(
                text=f"{name} | {price}$ | [{qty_text}]",
                callback_data=f"adm_prod_edit:{slug}",
            ),
            InlineKeyboardButton(text="❌", callback_data=f"adm_prod_del:{slug}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ დამატება", callback_data="adm_prod_add")])
    rows.append([InlineKeyboardButton(text="◀ უკან", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_edit_kb(slug: str) -> InlineKeyboardMarkup:
    """Edit individual fields of a product."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏ სახელი", callback_data=f"adm_pe:name:{slug}"),
         InlineKeyboardButton(text="💰 ფასი", callback_data=f"adm_pe:price:{slug}")],
        [InlineKeyboardButton(text="📝 აღწერა", callback_data=f"adm_pe:desc:{slug}"),
         InlineKeyboardButton(text="📦 რაოდენობა", callback_data=f"adm_pe:qty:{slug}")],
        [InlineKeyboardButton(text="🗺 უბნების მინიჭება", callback_data=f"adm_pd:{slug}")],
        [InlineKeyboardButton(text="◀ უკან", callback_data="admin:products")],
    ])


def product_districts_kb(slug: str, all_districts: dict, assigned: list[str]) -> InlineKeyboardMarkup:
    """Toggle keyboard: each district shows ✅ if assigned, ⬜ if not."""
    rows = []
    for d_slug, d_info in all_districts.items():
        check = "✅" if d_slug in assigned else "⬜"
        rows.append([
            InlineKeyboardButton(
                text=f"{check} {d_info['name']}",
                callback_data=f"adm_pd_toggle:{slug}|{d_slug}",
            )
        ])
    rows.append([InlineKeyboardButton(text="◀ უკან", callback_data=f"adm_prod_edit:{slug}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_delete_kb(slug: str, entity: str = "prod") -> InlineKeyboardMarkup:
    """Confirm deletion of a product or district."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ დიახ, წაშალე", callback_data=f"adm_{entity}_confirm_del:{slug}"),
         InlineKeyboardButton(text="❌ არა", callback_data=f"admin:{'products' if entity == 'prod' else 'districts'}")],
    ])


# ── District management ───────────────────────────────────────────────

def districts_menu_kb(districts: dict) -> InlineKeyboardMarkup:
    rows = []
    for slug, info in districts.items():
        rows.append([
            InlineKeyboardButton(text=info["name"], callback_data="noop"),
            InlineKeyboardButton(text="❌", callback_data=f"adm_dist_del:{slug}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ დამატება", callback_data="adm_dist_add")])
    rows.append([InlineKeyboardButton(text="◀ უკან", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Broadcast ──────────────────────────────────────────────────────────

def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 გაგზავნა", callback_data="adm_bc_send"),
         InlineKeyboardButton(text="❌ გაუქმება", callback_data="admin:menu")],
    ])


def broadcast_photo_kb() -> InlineKeyboardMarkup:
    """Ask if admin wants to attach a photo."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📷 ფოტოს დამატება", callback_data="adm_bc_photo")],
        [InlineKeyboardButton(text="➡ გაგრძელება ფოტოს გარეშე", callback_data="adm_bc_no_photo")],
        [InlineKeyboardButton(text="❌ გაუქმება", callback_data="admin:menu")],
    ])


# ── Scheduled announcements ───────────────────────────────────────────

def scheduled_menu_kb(announcements: list) -> InlineKeyboardMarkup:
    rows = []
    for ann in announcements:
        label = ann["text"][:30] + ("..." if len(ann["text"]) > 30 else "")
        photo_icon = "📷" if ann.get("photo") else ""
        recurring = f" [{ann['recurring']}]" if ann.get("recurring") else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{photo_icon}{label}{recurring} @ {ann['run_at']}",
                callback_data="noop",
            ),
            InlineKeyboardButton(text="❌", callback_data=f"adm_sched_del:{ann['id']}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ ახალი დაგეგმვა", callback_data="adm_sched_add")])
    rows.append([InlineKeyboardButton(text="◀ უკან", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recurring_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ერთჯერადი", callback_data="adm_rec:none")],
        [
            InlineKeyboardButton(text="30 წთ", callback_data="adm_rec:30m"),
            InlineKeyboardButton(text="1 სთ", callback_data="adm_rec:1h"),
            InlineKeyboardButton(text="2 სთ", callback_data="adm_rec:2h"),
        ],
        [
            InlineKeyboardButton(text="3 სთ", callback_data="adm_rec:3h"),
            InlineKeyboardButton(text="6 სთ", callback_data="adm_rec:6h"),
            InlineKeyboardButton(text="12 სთ", callback_data="adm_rec:12h"),
        ],
        [InlineKeyboardButton(text="24 სთ", callback_data="adm_rec:24h")],
        [InlineKeyboardButton(text="❌ გაუქმება", callback_data="admin:menu")],
    ])


def sched_photo_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📷 ფოტოს დამატება", callback_data="adm_sched_photo")],
        [InlineKeyboardButton(text="➡ გაგრძელება ფოტოს გარეშე", callback_data="adm_sched_no_photo")],
        [InlineKeyboardButton(text="❌ გაუქმება", callback_data="admin:menu")],
    ])


# ── Users ──────────────────────────────────────────────────────────────

def users_menu_kb(total: int, banned_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👥 სულ: {total} | 🚫 დაბლოკილი: {banned_count}",
                              callback_data="noop")],
        [InlineKeyboardButton(text="🚫 დაბლოკვა", callback_data="adm_user_ban"),
         InlineKeyboardButton(text="✅ განბლოკვა", callback_data="adm_user_unban")],
        [InlineKeyboardButton(text="💰 ბალანსის დაყენება", callback_data="adm_user_balance")],
        [InlineKeyboardButton(text="◀ უკან", callback_data="admin:menu")],
    ])


# ── Stats ──────────────────────────────────────────────────────────────

def stats_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ უკან", callback_data="admin:menu")],
    ])


# ── Generic ────────────────────────────────────────────────────────────

def admin_back_kb(target: str = "admin:menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ უკან", callback_data=target)],
    ])
