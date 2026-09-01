"""
Admin panel handlers for HADES PARFUMES bot.

Features:
  - Product management (add / edit / delete)
  - District management (add / delete)
  - Immediate broadcast (text + optional photo)
  - Scheduled announcements (one-time + recurring, with optional photo)
  - User management (view / ban / unban)
  - Statistics dashboard
  - Text command shortcuts (/admin, /addproduct, /broadcast, /stats)
"""

import asyncio
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

import admin_keyboards as akb
import bot_registry
import data_store as ds
from admin_states import (
    AddDistrictFSM,
    AddProductFSM,
    BanUserFSM,
    BroadcastFSM,
    EditProductFSM,
    PrivateDmFSM,
    ScheduledAnnouncementFSM,
    SetBalanceFSM,
)
from config import ADMIN_IDS

router = Router()


# ── Helpers ────────────────────────────────────────────────────────────

def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _slugify(name: str) -> str:
    """Turn a product name into a callback-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]", "_", name.lower()).strip("_")
    return f"product:{slug}"


# ── Admin menu (entry) ─────────────────────────────────────────────────

@router.callback_query(F.data == "admin:menu")
async def admin_menu_cb(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("არ გაქვთ წვდომა", show_alert=True)
        return
    await state.clear()
    # The main menu message is a photo, so edit_text won't work.
    # Delete the photo message and send a fresh text message.
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer(
        "⚙ ადმინ პანელი — HADES PARFUMES",
        reply_markup=akb.admin_menu_kb(),
    )
    await call.answer()


@router.message(Command("admin"))
async def admin_cmd(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "⚙ ადმინ პანელი — HADES PARFUMES",
        reply_markup=akb.admin_menu_kb(),
    )


# ══════════════════════════════════════════════════════════════════════
#  PRODUCTS
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:products")
async def products_list(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await state.clear()
    products = ds.get_products()
    await call.message.edit_text(
        "📦 პროდუქტების მართვა:",
        reply_markup=akb.products_menu_kb(products),
    )
    await call.answer()


# ── Add product ────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_prod_add")
async def add_product_start(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await call.message.edit_text(
        "📦 ახალი პროდუქტის სახელი:\n(მაგ: Prada Luna 50ml)",
        reply_markup=akb.admin_back_kb("admin:products"),
    )
    await state.set_state(AddProductFSM.waiting_name)
    await call.answer()


@router.message(Command("addproduct"))
async def add_product_cmd(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await message.answer(
        "📦 ახალი პროდუქტის სახელი:\n(მაგ: Prada Luna 50ml)",
        reply_markup=akb.admin_back_kb("admin:products"),
    )
    await state.set_state(AddProductFSM.waiting_name)


@router.message(AddProductFSM.waiting_name, F.text)
async def add_product_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer(
        "💰 ფასი დოლარში (მხოლოდ რიცხვი):\n(მაგ: 150)",
        reply_markup=akb.admin_back_kb("admin:products"),
    )
    await state.set_state(AddProductFSM.waiting_price)


@router.message(AddProductFSM.waiting_price, F.text)
async def add_product_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("❌ შეიყვანეთ რიცხვი. სცადეთ ხელახლა:")
        return
    await state.update_data(price=price)
    await message.answer(
        "📝 აღწერა (ან გამოტოვეთ - ჩაწერეთ '-'):",
        reply_markup=akb.admin_back_kb("admin:products"),
    )
    await state.set_state(AddProductFSM.waiting_description)


@router.message(AddProductFSM.waiting_description, F.text)
async def add_product_desc(message: Message, state: FSMContext):
    desc = message.text.strip()
    if desc == "-":
        desc = ""
    await state.update_data(description=desc)
    await message.answer(
        "📦 რაოდენობა (რიცხვი ან -1 = ულიმიტო):",
        reply_markup=akb.admin_back_kb("admin:products"),
    )
    await state.set_state(AddProductFSM.waiting_quantity)


@router.message(AddProductFSM.waiting_quantity, F.text)
async def add_product_qty(message: Message, state: FSMContext):
    try:
        qty = int(message.text.strip())
    except ValueError:
        await message.answer("❌ შეიყვანეთ მთელი რიცხვი. სცადეთ ხელახლა:")
        return
    data = await state.get_data()
    name = data["name"]
    price = data["price"]
    desc = data.get("description", "")
    slug = _slugify(name)

    # Build the button label: name + price only (quantity is internal, not shown to users)
    display_name = f"{name} {price}$"

    ds.add_product(slug, display_name, price, desc, qty)
    await state.clear()
    await message.answer(
        f"✅ პროდუქტი დამატებულია:\n{display_name}",
        reply_markup=akb.admin_back_kb("admin:products"),
    )


# ── Edit product ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_prod_edit:"))
async def edit_product_menu(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    slug = call.data.split(":", 2)[-1]  # "adm_prod_edit:product:xxx" → "product:xxx"
    slug = "product:" + slug.split("product:")[-1]
    product = ds.get_product(slug)
    if not product:
        await call.answer("პროდუქტი ვერ მოიძებნა", show_alert=True)
        return
    assigned_count = len(product.get("districts", []))
    await call.message.edit_text(
        f"✏ პროდუქტის რედაქტირება:\n\n"
        f"სახელი: {product['name']}\n"
        f"ფასი: {product['price_usd']}$\n"
        f"აღწერა: {product.get('description', '-')}\n"
        f"რაოდენობა: {'∞' if product['quantity'] == -1 else product['quantity']}\n"
        f"🗺 უბნები: {assigned_count} მინიჭებული",
        reply_markup=akb.product_edit_kb(slug),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_pe:"))
async def edit_product_field(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    # "adm_pe:name:product:xxx"
    parts = call.data.split(":", 2)  # ['adm_pe', 'name', 'product:xxx']
    field = parts[1]
    slug = parts[2]

    field_names = {
        "name": "სახელი",
        "price": "ფასი (რიცხვი)",
        "desc": "აღწერა",
        "qty": "რაოდენობა (რიცხვი ან -1)",
    }
    await call.message.edit_text(
        f"შეიყვანეთ ახალი {field_names.get(field, field)}:",
        reply_markup=akb.admin_back_kb("admin:products"),
    )
    await state.update_data(edit_slug=slug, edit_field=field)
    await state.set_state(EditProductFSM.waiting_new_value)
    await call.answer()


@router.message(EditProductFSM.waiting_new_value, F.text)
async def edit_product_value(message: Message, state: FSMContext):
    data = await state.get_data()
    slug = data["edit_slug"]
    field = data["edit_field"]
    value = message.text.strip()

    product = ds.get_product(slug)
    if not product:
        await message.answer("❌ პროდუქტი ვერ მოიძებნა")
        await state.clear()
        return

    if field == "name":
        ds.update_product(slug, name=value)
    elif field == "price":
        try:
            ds.update_product(slug, price_usd=float(value.replace(",", ".")))
        except ValueError:
            await message.answer("❌ შეიყვანეთ რიცხვი:")
            return
    elif field == "desc":
        ds.update_product(slug, description=value)
    elif field == "qty":
        try:
            ds.update_product(slug, quantity=int(value))
        except ValueError:
            await message.answer("❌ შეიყვანეთ მთელი რიცხვი:")
            return

    await state.clear()
    await message.answer(
        f"✅ პროდუქტი განახლებულია",
        reply_markup=akb.admin_back_kb("admin:products"),
    )


# ── Assign districts to product ───────────────────────────────────────

@router.callback_query(F.data.startswith("adm_pd:"))
async def product_districts_menu(call: CallbackQuery):
    """Show the district assignment toggle screen for a product."""
    if not _is_admin(call.from_user.id):
        return
    # "adm_pd:product:xxx"
    slug = "product:" + call.data.split("product:")[-1]
    product = ds.get_product(slug)
    if not product:
        await call.answer("პროდუქტი ვერ მოიძებნა", show_alert=True)
        return
    all_districts = ds.get_districts(product.get("city", "tbilisi"))
    assigned = ds.get_product_districts(slug)
    await call.message.edit_text(
        f"🗺 უბნების მინიჭება:\n{product['name']}\n\n"
        f"დააჭირეთ უბანს ჩასართავად/გამოსართავად:",
        reply_markup=akb.product_districts_kb(slug, all_districts, assigned),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_pd_toggle:"))
async def toggle_product_district(call: CallbackQuery):
    """Toggle a district on/off for a product, then refresh the same screen."""
    if not _is_admin(call.from_user.id):
        return
    # "adm_pd_toggle:product:xxx|district:yyy"
    payload = call.data.replace("adm_pd_toggle:", "")
    parts = payload.split("|")
    slug = parts[0]           # "product:xxx"
    district_slug = parts[1]  # "district:yyy"

    ds.toggle_product_district(slug, district_slug)

    # Refresh the same keyboard
    product = ds.get_product(slug)
    all_districts = ds.get_districts(product.get("city", "tbilisi"))
    assigned = ds.get_product_districts(slug)
    await call.message.edit_text(
        f"🗺 უბნების მინიჭება:\n{product['name']}\n\n"
        f"დააჭირეთ უბანს ჩასართავად/გამოსართავად:",
        reply_markup=akb.product_districts_kb(slug, all_districts, assigned),
    )
    await call.answer()


# ── Delete product ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_prod_del:"))
async def delete_product_ask(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    slug = "product:" + call.data.split("product:")[-1]
    product = ds.get_product(slug)
    name = product["name"] if product else slug
    await call.message.edit_text(
        f"❌ წაშალოთ პროდუქტი?\n{name}",
        reply_markup=akb.confirm_delete_kb(slug, "prod"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_prod_confirm_del:"))
async def delete_product_confirm(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    slug = "product:" + call.data.split("product:")[-1]
    ds.delete_product(slug)
    await call.message.edit_text(
        "✅ პროდუქტი წაშლილია",
        reply_markup=akb.admin_back_kb("admin:products"),
    )
    await call.answer()


# ══════════════════════════════════════════════════════════════════════
#  DISTRICTS
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:districts")
async def districts_list(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await state.clear()
    districts = ds.get_districts()
    await call.message.edit_text(
        "🗺 უბნების მართვა:",
        reply_markup=akb.districts_menu_kb(districts),
    )
    await call.answer()


@router.callback_query(F.data == "adm_dist_add")
async def add_district_start(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await call.message.edit_text(
        "🗺 ახალი უბნის სახელი:\n(მაგ: Vake, Saburtalo)",
        reply_markup=akb.admin_back_kb("admin:districts"),
    )
    await state.set_state(AddDistrictFSM.waiting_name)
    await call.answer()


@router.message(AddDistrictFSM.waiting_name, F.text)
async def add_district_name(message: Message, state: FSMContext):
    name = message.text.strip()
    slug = "district:" + re.sub(r"[^a-zA-Z0-9]", "_", name.lower()).strip("_")
    await state.update_data(dist_name=name, dist_slug=slug)
    await message.answer(
        "🖼 შეიყვანეთ სურათის ფაილის სახელი (მაგ: varketili.jpg)\n"
        "ან გამოტოვეთ — ჩაწერეთ '-':",
        reply_markup=akb.admin_back_kb("admin:districts"),
    )
    await state.set_state(AddDistrictFSM.waiting_image)


@router.message(AddDistrictFSM.waiting_image, F.text)
async def add_district_image(message: Message, state: FSMContext):
    image = message.text.strip()
    if image == "-":
        image = ""
    data = await state.get_data()
    ds.add_district(data["dist_slug"], data["dist_name"], image=image)
    await state.clear()
    img_text = f"\n🖼 სურათი: {image}" if image else ""
    await message.answer(
        f"✅ უბანი დამატებულია: {data['dist_name']}{img_text}",
        reply_markup=akb.admin_back_kb("admin:districts"),
    )


@router.callback_query(F.data.startswith("adm_dist_del:"))
async def delete_district_ask(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    slug = "district:" + call.data.split("district:")[-1]
    districts = ds.get_districts()
    name = districts.get(slug, {}).get("name", slug)
    await call.message.edit_text(
        f"❌ წაშალოთ უბანი?\n{name}",
        reply_markup=akb.confirm_delete_kb(slug, "dist"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm_dist_confirm_del:"))
async def delete_district_confirm(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    slug = "district:" + call.data.split("district:")[-1]
    ds.delete_district(slug)
    await call.message.edit_text(
        "✅ უბანი წაშლილია",
        reply_markup=akb.admin_back_kb("admin:districts"),
    )
    await call.answer()


# ══════════════════════════════════════════════════════════════════════
#  BROADCAST (immediate)
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start_cb(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await call.message.edit_text(
        "📢 ჩაწერეთ განცხადების ტექსტი:",
        reply_markup=akb.admin_back_kb("admin:menu"),
    )
    await state.set_state(BroadcastFSM.waiting_message)
    await call.answer()


@router.message(Command("broadcast"))
async def broadcast_start_cmd(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await message.answer(
        "📢 ჩაწერეთ განცხადების ტექსტი:",
        reply_markup=akb.admin_back_kb("admin:menu"),
    )
    await state.set_state(BroadcastFSM.waiting_message)


@router.message(BroadcastFSM.waiting_message, F.text)
async def broadcast_text_received(message: Message, state: FSMContext):
    await state.update_data(bc_text=message.text)
    await message.answer(
        "გსურთ ფოტოს დამატება?",
        reply_markup=akb.broadcast_photo_kb(),
    )
    await state.set_state(BroadcastFSM.waiting_photo)


@router.callback_query(F.data == "adm_bc_photo", BroadcastFSM.waiting_photo)
async def broadcast_ask_photo(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "📷 გამოაგზავნეთ ფოტო:",
        reply_markup=akb.admin_back_kb("admin:menu"),
    )
    await call.answer()


@router.message(BroadcastFSM.waiting_photo, F.photo)
async def broadcast_photo_received(message: Message, state: FSMContext):
    """Admin sent a photo — save the file_id."""
    photo = message.photo[-1]  # highest resolution
    await state.update_data(bc_photo_id=photo.file_id)
    data = await state.get_data()
    await message.answer(
        f"📢 განცხადების წინასწარი ხედვა:\n\n{data['bc_text']}\n\n📷 ფოტო: დამატებულია\n\nგსურთ გაგზავნა?",
        reply_markup=akb.broadcast_confirm_kb(),
    )
    await state.set_state(BroadcastFSM.waiting_confirm)


@router.callback_query(F.data == "adm_bc_no_photo", BroadcastFSM.waiting_photo)
async def broadcast_no_photo(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await call.message.edit_text(
        f"📢 განცხადების წინასწარი ხედვა:\n\n{data['bc_text']}\n\nგსურთ გაგზავნა?",
        reply_markup=akb.broadcast_confirm_kb(),
    )
    await state.set_state(BroadcastFSM.waiting_confirm)
    await call.answer()


@router.callback_query(F.data == "adm_bc_send", BroadcastFSM.waiting_confirm)
async def broadcast_execute(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    data = await state.get_data()
    text = data["bc_text"]
    photo_id = data.get("bc_photo_id")
    user_ids = ds.get_all_user_ids()

    await call.message.edit_text(f"📨 იგზავნება {len(user_ids)} მომხმარებელთან...")
    await call.answer()

    sent, failed, blocked = 0, 0, 0

    for uid in user_ids:
        try:
            if photo_id:
                await bot_registry.send_photo_all_bots(
                    chat_id=uid, photo=photo_id, caption=text,
                )
            else:
                await bot_registry.send_message_all_bots(
                    chat_id=uid, text=text,
                )
            sent += 1
        except Exception:
            failed += 1
        # Telegram rate limit: ~30 msgs/sec
        if (sent + failed) % 25 == 0:
            await asyncio.sleep(1)

    await state.clear()
    await call.message.edit_text(
        f"✅ განცხადება გაგზავნილია!\n\n"
        f"📨 გაგზავნილი: {sent}\n"
        f"❌ ვერ გაიგზავნა: {failed}\n"
        f"👥 სულ: {len(user_ids)}",
        reply_markup=akb.admin_back_kb("admin:menu"),
    )


# ══════════════════════════════════════════════════════════════════════
#  PRIVATE DM  (send message to specific user IDs)
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:dm")
async def dm_start_cb(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await call.message.edit_text(
        "✉ შეიყვანეთ მომხმარებლის ID-ები (მძიმით გამოყოფილი):\n\n"
        "მაგ: 123456789, 987654321",
        reply_markup=akb.admin_back_kb("admin:menu"),
    )
    await state.set_state(PrivateDmFSM.waiting_user_ids)
    await call.answer()


@router.message(Command("dm"))
async def dm_start_cmd(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await message.answer(
        "✉ შეიყვანეთ მომხმარებლის ID-ები (მძიმით გამოყოფილი):\n\n"
        "მაგ: 123456789, 987654321",
        reply_markup=akb.admin_back_kb("admin:menu"),
    )
    await state.set_state(PrivateDmFSM.waiting_user_ids)


@router.message(PrivateDmFSM.waiting_user_ids, F.text)
async def dm_ids_received(message: Message, state: FSMContext):
    raw = message.text.replace(" ", "")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    valid_ids = []
    for p in parts:
        if p.lstrip("-").isdigit():
            valid_ids.append(int(p))
    if not valid_ids:
        await message.answer(
            "❌ ვერცერთი ID ვერ მოიძებნა. სცადეთ ხელახლა:\n"
            "მაგ: 123456789, 987654321",
        )
        return
    await state.update_data(dm_user_ids=valid_ids)
    await message.answer(
        f"✅ {len(valid_ids)} მომხმარებელი არჩეულია.\n\n"
        "ახლა ჩაწერეთ შეტყობინების ტექსტი:",
        reply_markup=akb.admin_back_kb("admin:menu"),
    )
    await state.set_state(PrivateDmFSM.waiting_message)


@router.message(PrivateDmFSM.waiting_message, F.text)
async def dm_text_received(message: Message, state: FSMContext):
    await state.update_data(dm_text=message.text)
    await message.answer(
        "გსურთ ფოტოს დამატება?",
        reply_markup=akb.dm_photo_kb(),
    )
    await state.set_state(PrivateDmFSM.waiting_photo)


@router.callback_query(F.data == "adm_dm_photo", PrivateDmFSM.waiting_photo)
async def dm_ask_photo(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "📷 გამოაგზავნეთ ფოტო:",
        reply_markup=akb.admin_back_kb("admin:menu"),
    )
    await call.answer()


@router.message(PrivateDmFSM.waiting_photo, F.photo)
async def dm_photo_received(message: Message, state: FSMContext):
    photo = message.photo[-1]
    await state.update_data(dm_photo_id=photo.file_id)
    data = await state.get_data()
    ids = data["dm_user_ids"]
    await message.answer(
        f"✉ პირადი შეტყობინების წინასწარი ხედვა:\n\n"
        f"{data['dm_text']}\n\n"
        f"📷 ფოტო: დამატებულია\n"
        f"👥 მიმღებები: {len(ids)} მომხმარებელი\n\n"
        f"გსურთ გაგზავნა?",
        reply_markup=akb.dm_confirm_kb(),
    )
    await state.set_state(PrivateDmFSM.waiting_confirm)


@router.callback_query(F.data == "adm_dm_no_photo", PrivateDmFSM.waiting_photo)
async def dm_no_photo(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ids = data["dm_user_ids"]
    await call.message.edit_text(
        f"✉ პირადი შეტყობინების წინასწარი ხედვა:\n\n"
        f"{data['dm_text']}\n\n"
        f"👥 მიმღებები: {len(ids)} მომხმარებელი\n\n"
        f"გსურთ გაგზავნა?",
        reply_markup=akb.dm_confirm_kb(),
    )
    await state.set_state(PrivateDmFSM.waiting_confirm)
    await call.answer()


@router.callback_query(F.data == "adm_dm_send", PrivateDmFSM.waiting_confirm)
async def dm_execute(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    data = await state.get_data()
    text = data["dm_text"]
    photo_id = data.get("dm_photo_id")
    user_ids = data["dm_user_ids"]

    await call.message.edit_text(f"📨 იგზავნება {len(user_ids)} მომხმარებელთან...")
    await call.answer()

    sent, failed = 0, 0
    for uid in user_ids:
        try:
            if photo_id:
                await bot_registry.send_photo_all_bots(
                    chat_id=uid, photo=photo_id, caption=text,
                )
            else:
                await bot_registry.send_message_all_bots(
                    chat_id=uid, text=text,
                )
            sent += 1
        except Exception:
            failed += 1
        if (sent + failed) % 25 == 0:
            await asyncio.sleep(1)

    await state.clear()
    await call.message.edit_text(
        f"✅ პირადი შეტყობინება გაგზავნილია!\n\n"
        f"📨 გაგზავნილი: {sent}\n"
        f"❌ ვერ გაიგზავნა: {failed}\n"
        f"👥 სულ: {len(user_ids)}",
        reply_markup=akb.admin_back_kb("admin:menu"),
    )


# ══════════════════════════════════════════════════════════════════════
#  SCHEDULED ANNOUNCEMENTS
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:scheduled")
async def scheduled_list(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await state.clear()
    pending = ds.get_pending_announcements()
    await call.message.edit_text(
        "📅 დაგეგმილი განცხადებები:",
        reply_markup=akb.scheduled_menu_kb(pending),
    )
    await call.answer()


@router.callback_query(F.data == "adm_sched_add")
async def sched_add_start(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await call.message.edit_text(
        "📅 ჩაწერეთ განცხადების ტექსტი:",
        reply_markup=akb.admin_back_kb("admin:scheduled"),
    )
    await state.set_state(ScheduledAnnouncementFSM.waiting_message)
    await call.answer()


@router.message(ScheduledAnnouncementFSM.waiting_message, F.text)
async def sched_text_received(message: Message, state: FSMContext):
    await state.update_data(sched_text=message.text)
    await message.answer(
        "გსურთ ფოტოს დამატება?",
        reply_markup=akb.sched_photo_kb(),
    )
    await state.set_state(ScheduledAnnouncementFSM.waiting_photo)


@router.callback_query(F.data == "adm_sched_photo", ScheduledAnnouncementFSM.waiting_photo)
async def sched_ask_photo(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "📷 გამოაგზავნეთ ფოტო:",
        reply_markup=akb.admin_back_kb("admin:scheduled"),
    )
    await call.answer()


@router.message(ScheduledAnnouncementFSM.waiting_photo, F.photo)
async def sched_photo_received(message: Message, state: FSMContext):
    photo = message.photo[-1]
    await state.update_data(sched_photo_id=photo.file_id)
    await message.answer(
        "🔄 აირჩიეთ განმეორების ტიპი:",
        reply_markup=akb.recurring_kb(),
    )
    await state.set_state(ScheduledAnnouncementFSM.waiting_recurring)


@router.callback_query(F.data == "adm_sched_no_photo", ScheduledAnnouncementFSM.waiting_photo)
async def sched_no_photo(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "🔄 აირჩიეთ განმეორების ტიპი:",
        reply_markup=akb.recurring_kb(),
    )
    await state.set_state(ScheduledAnnouncementFSM.waiting_recurring)
    await call.answer()


# Interval labels for display
_INTERVAL_LABELS = {
    "30m": "30 წუთი",
    "1h": "1 საათი",
    "2h": "2 საათი",
    "3h": "3 საათი",
    "6h": "6 საათი",
    "12h": "12 საათი",
    "24h": "24 საათი",
}


def _interval_to_minutes(interval: str) -> int:
    """Convert interval string like '30m', '2h' to minutes."""
    if interval.endswith("m"):
        return int(interval[:-1])
    elif interval.endswith("h"):
        return int(interval[:-1]) * 60
    return 60


@router.callback_query(F.data.startswith("adm_rec:"), ScheduledAnnouncementFSM.waiting_recurring)
async def sched_recurring_chosen(call: CallbackQuery, state: FSMContext):
    rec_type = call.data.replace("adm_rec:", "")

    if rec_type == "none":
        # One-time: ask for date/time
        await call.message.edit_text(
            "📅 შეიყვანეთ თარიღი და დრო:\n(ფორმატი: 2025-06-15 14:00)",
            reply_markup=akb.admin_back_kb("admin:scheduled"),
        )
        await state.update_data(sched_recurring=None)
        await state.set_state(ScheduledAnnouncementFSM.waiting_datetime)
        await call.answer()
        return

    # Interval-based: start immediately (first send = now + interval)
    data = await state.get_data()
    minutes = _interval_to_minutes(rec_type)
    first_run = datetime.now() + timedelta(minutes=minutes)
    run_at_str = first_run.strftime("%Y-%m-%d %H:%M")

    ann_id = ds.add_scheduled_announcement(
        text=data["sched_text"],
        run_at=run_at_str,
        recurring=rec_type,
        photo_path=data.get("sched_photo_id"),
    )
    await state.clear()
    label = _INTERVAL_LABELS.get(rec_type, rec_type)
    await call.message.edit_text(
        f"✅ განცხადება დაგეგმილია!\n\n"
        f"🔄 ინტერვალი: ყოველ {label}\n"
        f"📅 პირველი გაგზავნა: {run_at_str}\n"
        f"🆔 ID: {ann_id}",
        reply_markup=akb.admin_back_kb("admin:scheduled"),
    )
    await call.answer()


@router.message(ScheduledAnnouncementFSM.waiting_datetime, F.text)
async def sched_datetime_received(message: Message, state: FSMContext):
    raw = message.text.strip()
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
        if dt <= datetime.now():
            await message.answer("❌ თარიღი უნდა იყოს მომავალში. სცადეთ ხელახლა:")
            return
    except ValueError:
        await message.answer("❌ არასწორი ფორმატი. გამოიყენეთ: 2025-06-15 14:00")
        return

    data = await state.get_data()
    ann_id = ds.add_scheduled_announcement(
        text=data["sched_text"],
        run_at=raw,
        recurring=None,
        photo_path=data.get("sched_photo_id"),
    )
    await state.clear()
    await message.answer(
        f"✅ განცხადება დაგეგმილია!\n\n"
        f"📅 დრო: {raw}\n"
        f"🔄 ტიპი: ერთჯერადი\n"
        f"🆔 ID: {ann_id}",
        reply_markup=akb.admin_back_kb("admin:scheduled"),
    )


@router.callback_query(F.data.startswith("adm_sched_del:"))
async def sched_delete(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    ann_id = int(call.data.replace("adm_sched_del:", ""))
    ds.delete_scheduled_announcement(ann_id)
    # Refresh the list
    pending = ds.get_pending_announcements()
    await call.message.edit_text(
        "📅 დაგეგმილი განცხადებები:",
        reply_markup=akb.scheduled_menu_kb(pending),
    )
    await call.answer("წაშლილია", show_alert=False)


# ══════════════════════════════════════════════════════════════════════
#  USER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:users")
async def users_menu(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await state.clear()
    total = ds.user_count()
    all_ids = ds.get_all_user_ids()
    banned = sum(1 for uid in all_ids if ds.is_user_banned(uid))
    await call.message.edit_text(
        "👥 მომხმარებლების მართვა:",
        reply_markup=akb.users_menu_kb(total, banned),
    )
    await call.answer()


@router.callback_query(F.data == "adm_user_ban")
async def ban_user_start(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await call.message.edit_text(
        "🚫 შეიყვანეთ მომხმარებლის Telegram ID დასაბლოკად:",
        reply_markup=akb.admin_back_kb("admin:users"),
    )
    await state.update_data(ban_action="ban")
    await state.set_state(BanUserFSM.waiting_user_id)
    await call.answer()


@router.callback_query(F.data == "adm_user_unban")
async def unban_user_start(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await call.message.edit_text(
        "✅ შეიყვანეთ მომხმარებლის Telegram ID განსაბლოკად:",
        reply_markup=akb.admin_back_kb("admin:users"),
    )
    await state.update_data(ban_action="unban")
    await state.set_state(BanUserFSM.waiting_user_id)
    await call.answer()


@router.message(BanUserFSM.waiting_user_id, F.text)
async def ban_user_execute(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ შეიყვანეთ რიცხვი (Telegram ID):")
        return

    data = await state.get_data()
    action = data.get("ban_action", "ban")
    is_ban = action == "ban"

    if ds.set_user_banned(target_id, is_ban):
        status = "დაბლოკილია 🚫" if is_ban else "განბლოკილია ✅"
        await message.answer(
            f"მომხმარებელი {target_id}: {status}",
            reply_markup=akb.admin_back_kb("admin:users"),
        )
    else:
        await message.answer(
            f"❌ მომხმარებელი {target_id} ვერ მოიძებნა",
            reply_markup=akb.admin_back_kb("admin:users"),
        )
    await state.clear()


# ── Set user balance ─────────────────────────────────────────────────

@router.callback_query(F.data == "adm_user_balance")
async def set_balance_start(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        return
    await call.message.edit_text(
        "💰 შეიყვანეთ მომხმარებლის Telegram ID:",
        reply_markup=akb.admin_back_kb("admin:users"),
    )
    await state.set_state(SetBalanceFSM.waiting_user_id)
    await call.answer()


@router.message(SetBalanceFSM.waiting_user_id, F.text)
async def set_balance_user_id(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ შეიყვანეთ რიცხვი (Telegram ID):")
        return

    user = ds.get_user(target_id)
    if not user:
        await message.answer(
            f"❌ მომხმარებელი {target_id} ვერ მოიძებნა",
            reply_markup=akb.admin_back_kb("admin:users"),
        )
        await state.clear()
        return

    current_balance = ds.get_user_balance(target_id)
    await state.update_data(balance_user_id=target_id)
    await message.answer(
        f"💰 მომხმარებელი: {target_id}\n"
        f"მიმდინარე ბალანსი: {current_balance} $\n\n"
        f"შეიყვანეთ ახალი ბალანსი (რიცხვი):",
        reply_markup=akb.admin_back_kb("admin:users"),
    )
    await state.set_state(SetBalanceFSM.waiting_amount)


@router.message(SetBalanceFSM.waiting_amount, F.text)
async def set_balance_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("❌ შეიყვანეთ რიცხვი. სცადეთ ხელახლა:")
        return

    data = await state.get_data()
    target_id = data["balance_user_id"]

    if ds.set_user_balance(target_id, amount):
        await message.answer(
            f"✅ ბალანსი განახლებულია!\n\n"
            f"მომხმარებელი: {target_id}\n"
            f"ახალი ბალანსი: {amount} $",
            reply_markup=akb.admin_back_kb("admin:users"),
        )
        # Notify the user naturally
        lang = ds.get_user_lang(target_id)
        if lang == "ru":
            note = f"💰 Ваш баланс обновлён: {amount} $"
        elif lang == "en":
            note = f"💰 Your balance has been updated: {amount} $"
        else:
            note = f"💰 თქვენი ბალანსი განახლდა: {amount} $"
        await bot_registry.send_message_all_bots(chat_id=target_id, text=note)
    else:
        await message.answer(
            f"❌ მომხმარებელი {target_id} ვერ მოიძებნა",
            reply_markup=akb.admin_back_kb("admin:users"),
        )
    await state.clear()


# ══════════════════════════════════════════════════════════════════════
#  STATISTICS
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:stats")
async def stats_dashboard(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    stats = ds.get_stats()
    text = (
        "📊 სტატისტიკა — HADES PARFUMES\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 მომხმარებლები: {stats['user_count']}\n"
        f"📦 პროდუქტები: {stats['product_count']}\n"
        f"🗺 უბნები: {stats['district_count']}\n\n"
        f"🛒 შეკვეთები: {stats['total_orders']}\n"
        f"💰 შემოსავალი: {stats['total_revenue_usd']}$\n\n"
        f"📅 დაგეგმილი განცხადებები: {stats['scheduled_announcements']}"
    )
    await call.message.edit_text(text, reply_markup=akb.stats_back_kb())
    await call.answer()


@router.message(Command("stats"))
async def stats_cmd(message: Message):
    if not _is_admin(message.from_user.id):
        return
    stats = ds.get_stats()
    text = (
        "📊 სტატისტიკა — HADES PARFUMES\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 მომხმარებლები: {stats['user_count']}\n"
        f"📦 პროდუქტები: {stats['product_count']}\n"
        f"🗺 უბნები: {stats['district_count']}\n\n"
        f"🛒 შეკვეთები: {stats['total_orders']}\n"
        f"💰 შემოსავალი: {stats['total_revenue_usd']}$\n\n"
        f"📅 დაგეგმილი განცხადებები: {stats['scheduled_announcements']}"
    )
    await message.answer(text, reply_markup=akb.stats_back_kb())


# ══════════════════════════════════════════════════════════════════════
#  SCHEDULER BACKGROUND TASK
# ══════════════════════════════════════════════════════════════════════

async def _check_scheduled_announcements(bot: Bot):
    """Check and send due scheduled announcements. Runs as a background loop."""
    while True:
        try:
            pending = ds.get_pending_announcements()
            now = datetime.now()
            for ann in pending:
                try:
                    run_at = datetime.strptime(ann["run_at"], "%Y-%m-%d %H:%M")
                except ValueError:
                    continue

                if run_at <= now:
                    user_ids = ds.get_all_user_ids()
                    for idx, uid in enumerate(user_ids):
                        if ann.get("photo"):
                            await bot_registry.send_photo_all_bots(
                                chat_id=uid,
                                photo=ann["photo"],
                                caption=ann["text"],
                            )
                        else:
                            await bot_registry.send_message_all_bots(
                                chat_id=uid, text=ann["text"],
                            )
                        if idx % 25 == 0 and idx > 0:
                            await asyncio.sleep(1)

                    # Handle recurring (interval-based)
                    if ann.get("recurring"):
                        rec = ann["recurring"]
                        if rec.endswith("m"):
                            delta = timedelta(minutes=int(rec[:-1]))
                        elif rec.endswith("h"):
                            delta = timedelta(hours=int(rec[:-1]))
                        else:
                            # Legacy fallback
                            delta_map = {
                                "daily": timedelta(days=1),
                                "weekly": timedelta(weeks=1),
                                "monthly": timedelta(days=30),
                            }
                            delta = delta_map.get(rec)
                        if delta:
                            next_dt = now + delta
                            ds.add_scheduled_announcement(
                                text=ann["text"],
                                run_at=next_dt.strftime("%Y-%m-%d %H:%M"),
                                recurring=rec,
                                photo_path=ann.get("photo"),
                            )
                    ds.mark_announcement_sent(ann["id"])
        except Exception:
            pass

        await asyncio.sleep(30)  # check every 30 seconds


def start_scheduler(bot: Bot):
    """Start the announcement scheduler as a background asyncio task."""
    asyncio.create_task(_check_scheduled_announcements(bot))
