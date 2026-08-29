import asyncio
import re
import secrets

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message

import bot_registry
import data_store as ds
import keyboards
import texts
from config import (
    ADMIN_IDS,
    BOUGHT_IMAGE_PATH,
    CRYPTO_WALLETS,
    GROUPS_IMAGE_PATH,
    LISTING_IMAGE_PATH,
    MENU_IMAGE_PATH,
    MOSKOVIS_IMAGE_PATH,
    VAKANSIA_IMAGE_PATH,
    district_image_path,
)
from qr_generator import generate_qr

router = Router()


def _generate_session_id() -> str:
    """Generate a random hex session ID (32 bytes = 64 hex chars)."""
    return secrets.token_hex(32)


def _extract_price(product_text: str) -> float:
    """Pull the dollar price from a product string like 'Prada (2) 150$'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*\$", product_text)
    return float(m.group(1)) if m else 0.0


def _lang(user_id: int) -> str:
    """Get the user's language preference."""
    return ds.get_user_lang(user_id)


async def _notify_admins_new_user(bot, user) -> None:
    """Send a notification to all admin users about a new user via ALL bots."""
    full_name = user.full_name or "Unknown"
    username = f"@{user.username}" if user.username else "no username"
    text = texts.NEW_USER_NOTIFICATION.format(
        full_name=full_name,
        username=username,
        user_id=user.id,
    )
    for admin_id in ADMIN_IDS:
        await bot_registry.send_message_all_bots(chat_id=admin_id, text=text)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    # Register user in data store
    is_new = ds.register_user(message.from_user.id, message.from_user.username or "")
    lang = _lang(message.from_user.id)

    # Notify admins about new user
    if is_new:
        await _notify_admins_new_user(message.bot, message.from_user)

    balance = ds.get_user_balance(message.from_user.id)
    await message.answer_photo(
        photo=FSInputFile(MENU_IMAGE_PATH),
        caption=texts.t("MAIN_MENU_CAPTION", lang),
        reply_markup=keyboards.main_menu_kb(user_id=message.from_user.id, lang=lang, balance=balance),
    )


@router.callback_query(F.data == "menu:cities")
async def on_cities(callback: CallbackQuery) -> None:
    lang = _lang(callback.from_user.id)
    await callback.message.edit_caption(
        caption=texts.t("CHOOSE_CITY_TEXT", lang),
        reply_markup=keyboards.cities_kb(lang),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:main")
async def on_back_to_main(callback: CallbackQuery) -> None:
    lang = _lang(callback.from_user.id)
    balance = ds.get_user_balance(callback.from_user.id)
    if callback.message.photo:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile(MENU_IMAGE_PATH),
                caption=texts.t("MAIN_MENU_CAPTION", lang),
            ),
            reply_markup=keyboards.main_menu_kb(user_id=callback.from_user.id, lang=lang, balance=balance),
        )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=FSInputFile(MENU_IMAGE_PATH),
            caption=texts.t("MAIN_MENU_CAPTION", lang),
            reply_markup=keyboards.main_menu_kb(user_id=callback.from_user.id, lang=lang, balance=balance),
        )
    await callback.answer()


@router.callback_query(F.data == "city:tbilisi")
async def on_city_tbilisi(callback: CallbackQuery) -> None:
    lang = _lang(callback.from_user.id)
    caption = texts.t("CITY_SELECTED_CAPTION", lang).format(
        city=texts.t("TBILISI_BUTTON", lang),
    )
    if callback.message.photo:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile(LISTING_IMAGE_PATH),
                caption=caption,
            ),
            reply_markup=keyboards.tbilisi_products_kb(lang),
        )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=FSInputFile(LISTING_IMAGE_PATH),
            caption=caption,
            reply_markup=keyboards.tbilisi_products_kb(lang),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("product:"))
async def on_product_selected(callback: CallbackQuery) -> None:
    """Product tapped -> delete photo, show product details as text + district picker."""
    lang = _lang(callback.from_user.id)
    product_cb = callback.data
    products = ds.get_products()
    product_info = products.get(product_cb, {})
    product_name = product_info.get("name", product_cb)
    description = product_info.get("description", "+AAA")
    session_id = _generate_session_id()

    text = texts.t("PRODUCT_DETAIL_TEXT", lang).format(
        city=texts.t("TBILISI_BUTTON", lang),
        product=product_name,
        description=description,
        session_id=session_id,
    )

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        text=text,
        parse_mode="HTML",
        reply_markup=keyboards.tbilisi_districts_kb(product_cb, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("district:"))
async def on_district_selected(callback: CallbackQuery) -> None:
    """District chosen -> show order confirmation with crypto payment options."""
    lang = _lang(callback.from_user.id)
    parts = callback.data.split("|", 1)
    district_cb = parts[0]
    product_cb = parts[1] if len(parts) > 1 else ""

    districts = ds.get_districts()
    products = ds.get_products()
    district_info = districts.get(district_cb, {})
    district_name = district_info.get("name", district_cb)
    district_img = district_info.get("image", "")
    product_info = products.get(product_cb, {})
    product_name = product_info.get("name", product_cb)
    description = product_info.get("description", "+AAA")
    price_usd = product_info.get("price_usd", _extract_price(product_name))

    session_id = _generate_session_id()
    user_balance = ds.get_user_balance(callback.from_user.id)

    caption = texts.t("ORDER_CONFIRMATION_CAPTION", lang).format(
        city=texts.t("TBILISI_BUTTON", lang),
        district=district_name,
        product=product_name,
        description=description,
        session_id=session_id,
        balance=user_balance,
    )

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer_photo(
        photo=FSInputFile(district_image_path(district_img)),
        caption=caption,
        parse_mode="HTML",
        reply_markup=keyboards.order_confirmation_kb(price_usd, product_cb, district_cb, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay:"))
async def on_crypto_payment(callback: CallbackQuery) -> None:
    """Crypto payment button -> show sticker, then QR code with wallet address."""
    lang = _lang(callback.from_user.id)
    parts = callback.data.split("|")
    crypto = parts[0].replace("pay:", "")
    product_cb = parts[1] if len(parts) > 1 else ""
    district_cb = parts[2] if len(parts) > 2 else ""

    products = ds.get_products()
    districts = ds.get_districts()
    product_info = products.get(product_cb, {})
    product_name = product_info.get("name", product_cb)
    district_name = districts.get(district_cb, {}).get("name", district_cb)
    price_usd = product_info.get("price_usd", _extract_price(product_name))

    wallet_address = CRYPTO_WALLETS.get(crypto, "")
    rates = texts.t_rates(lang)
    crypto_amount = round(price_usd * rates[crypto]["rate"], 6)

    try:
        await callback.message.delete()
    except Exception:
        pass
    sticker_msg = await callback.message.answer_sticker(sticker=texts.DEPOSIT_STICKER_ID)
    await asyncio.sleep(3)
    try:
        await sticker_msg.delete()
    except Exception:
        pass

    qr_path = generate_qr(wallet_address, crypto)

    caption = texts.CRYPTO_PAYMENT_CAPTION.format(
        coin=crypto,
        wallet_address=wallet_address,
        crypto_amount=crypto_amount,
        product=product_name,
        city=texts.t("TBILISI_BUTTON", lang),
        district=district_name,
    )

    await callback.message.answer_photo(
        photo=FSInputFile(qr_path),
        caption=caption,
        parse_mode="Markdown",
        reply_markup=keyboards.payment_back_kb(district_cb, product_cb, lang),
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════
#  BALANCE
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:balance")
async def on_balance(callback: CallbackQuery) -> None:
    """Show balance screen with crypto deposit options."""
    lang = _lang(callback.from_user.id)
    user_balance = ds.get_user_balance(callback.from_user.id)
    caption = texts.t("BALANCE_CAPTION", lang).format(balance=user_balance)
    if callback.message.photo:
        await callback.message.edit_media(
            media=InputMediaPhoto(media=FSInputFile(MENU_IMAGE_PATH), caption=caption),
            reply_markup=keyboards.balance_kb(lang),
        )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=FSInputFile(MENU_IMAGE_PATH),
            caption=caption,
            reply_markup=keyboards.balance_kb(lang),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("deposit:"))
async def on_deposit_crypto(callback: CallbackQuery) -> None:
    """Crypto deposit button -> show sticker, then QR code with wallet address."""
    lang = _lang(callback.from_user.id)
    crypto = callback.data.replace("deposit:", "")
    wallet_address = CRYPTO_WALLETS.get(crypto, "")

    try:
        await callback.message.delete()
    except Exception:
        pass
    sticker_msg = await callback.message.answer_sticker(sticker=texts.DEPOSIT_STICKER_ID)
    await asyncio.sleep(3)
    try:
        await sticker_msg.delete()
    except Exception:
        pass

    qr_path = generate_qr(wallet_address, crypto)

    caption = texts.BALANCE_DEPOSIT_CAPTION.format(
        coin=crypto,
        wallet_address=wallet_address,
    )

    await callback.message.answer_photo(
        photo=FSInputFile(qr_path),
        caption=caption,
        parse_mode="Markdown",
        reply_markup=keyboards.deposit_back_kb(crypto, lang),
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════
#  PURCHASES
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:purchases")
async def on_purchases(callback: CallbackQuery) -> None:
    """Show purchases screen with bought.jpg image."""
    lang = _lang(callback.from_user.id)
    if callback.message.photo:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile(BOUGHT_IMAGE_PATH),
                caption=texts.t("PURCHASES_CAPTION", lang),
            ),
            reply_markup=keyboards.purchases_kb(lang),
        )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=FSInputFile(BOUGHT_IMAGE_PATH),
            caption=texts.t("PURCHASES_CAPTION", lang),
            reply_markup=keyboards.purchases_kb(lang),
        )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════
#  REFERRAL
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:referral")
async def on_referral(callback: CallbackQuery) -> None:
    """Show referral screen with menu.jpg image and referral link."""
    lang = _lang(callback.from_user.id)
    bot_info = await callback.bot.get_me()
    caption = texts.t("REFERRAL_CAPTION", lang).format(
        referrals=0,
        earned=0.0,
        bot_username=bot_info.username,
        user_id=callback.from_user.id,
    )
    if callback.message.photo:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile(MENU_IMAGE_PATH),
                caption=caption,
            ),
            reply_markup=keyboards.referral_kb(lang),
        )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=FSInputFile(MENU_IMAGE_PATH),
            caption=caption,
            reply_markup=keyboards.referral_kb(lang),
        )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════
#  WORK
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:work")
async def on_work(callback: CallbackQuery) -> None:
    """Show work/vacancy screen with vakansia.jpg image."""
    lang = _lang(callback.from_user.id)
    if callback.message.photo:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile(VAKANSIA_IMAGE_PATH),
                caption=texts.t("WORK_CAPTION", lang),
            ),
            reply_markup=keyboards.work_kb(lang),
        )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=FSInputFile(VAKANSIA_IMAGE_PATH),
            caption=texts.t("WORK_CAPTION", lang),
            reply_markup=keyboards.work_kb(lang),
        )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════
#  GROUPS & CHANNELS
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:groups")
async def on_groups(callback: CallbackQuery) -> None:
    """Show groups & channels screen with groups.jpg image."""
    lang = _lang(callback.from_user.id)
    if callback.message.photo:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile(GROUPS_IMAGE_PATH),
                caption=texts.t("GROUPS_CAPTION", lang),
            ),
            reply_markup=keyboards.groups_kb(lang),
        )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=FSInputFile(GROUPS_IMAGE_PATH),
            caption=texts.t("GROUPS_CAPTION", lang),
            reply_markup=keyboards.groups_kb(lang),
        )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════
#  LANGUAGE
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:language")
async def on_language(callback: CallbackQuery) -> None:
    """Show language selection screen."""
    lang = _lang(callback.from_user.id)
    if callback.message.photo:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile(MENU_IMAGE_PATH),
                caption=texts.t("LANGUAGE_CAPTION", lang),
            ),
            reply_markup=keyboards.language_kb(lang),
        )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=FSInputFile(MENU_IMAGE_PATH),
            caption=texts.t("LANGUAGE_CAPTION", lang),
            reply_markup=keyboards.language_kb(lang),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("lang:"))
async def on_language_selected(callback: CallbackQuery) -> None:
    """User selected a language -> save preference and refresh main menu."""
    lang = callback.data.replace("lang:", "")
    ds.set_user_lang(callback.from_user.id, lang)
    balance = ds.get_user_balance(callback.from_user.id)

    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile(MENU_IMAGE_PATH),
            caption=texts.t("MAIN_MENU_CAPTION", lang),
        ),
        reply_markup=keyboards.main_menu_kb(user_id=callback.from_user.id, lang=lang, balance=balance),
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════
#  RESERVE BOTS
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:reserve_bots")
async def on_reserve_bots(callback: CallbackQuery) -> None:
    """Show reserve bots list with menu.jpg image."""
    lang = _lang(callback.from_user.id)
    if callback.message.photo:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=FSInputFile(MENU_IMAGE_PATH),
                caption=texts.t("RESERVE_BOTS_CAPTION", lang),
            ),
            reply_markup=keyboards.reserve_bots_kb(lang),
        )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=FSInputFile(MENU_IMAGE_PATH),
            caption=texts.t("RESERVE_BOTS_CAPTION", lang),
            reply_markup=keyboards.reserve_bots_kb(lang),
        )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════
#  PLACEHOLDERS + NOOP
# ══════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    await callback.answer()
