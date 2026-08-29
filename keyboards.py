from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import data_store as ds
import texts
from config import ADMIN_IDS


def main_menu_kb(user_id: int = 0, lang: str = "ka", balance: float = 0.0) -> InlineKeyboardMarkup:
    balance_text = texts.t("BALANCE_BUTTON", lang).format(balance=balance)
    rows = [
        [
            InlineKeyboardButton(text=texts.t("CITIES_BUTTON", lang), callback_data="menu:cities"),
            InlineKeyboardButton(text=balance_text, callback_data="menu:balance"),
        ],
        [
            InlineKeyboardButton(text=texts.t("PURCHASES_BUTTON", lang), callback_data="menu:purchases"),
            InlineKeyboardButton(text=texts.t("REFERRAL_BUTTON", lang), callback_data="menu:referral"),
        ],
        [
            InlineKeyboardButton(text=texts.t("WORK_BUTTON", lang), callback_data="menu:work"),
            InlineKeyboardButton(text=texts.t("GROUPS_BUTTON", lang), callback_data="menu:groups"),
        ],
        [
            InlineKeyboardButton(text=texts.t("LANGUAGE_BUTTON", lang), callback_data="menu:language"),
            InlineKeyboardButton(text=texts.t("RESERVE_BOTS_BUTTON", lang), callback_data="menu:reserve_bots"),
        ],
    ]
    # Admin button — visible only to admin IDs
    if user_id in ADMIN_IDS:
        rows.append([
            InlineKeyboardButton(text="⚙ ადმინ პანელი", callback_data="admin:menu"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cities_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.t("TBILISI_BUTTON", lang), callback_data="city:tbilisi")],
            [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:main")],
        ]
    )


def tbilisi_products_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    """Build product list dynamically from data store."""
    products = ds.get_products("tbilisi")
    rows = []
    for slug, info in products.items():
        rows.append([InlineKeyboardButton(text=info["name"], callback_data=slug)])
    rows.append([InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tbilisi_districts_kb(product_callback: str, lang: str = "ka") -> InlineKeyboardMarkup:
    """District picker for Tbilisi. Only shows districts assigned to this product."""
    all_districts = ds.get_districts("tbilisi")
    assigned = ds.get_product_districts(product_callback)
    rows = []
    for slug, info in all_districts.items():
        if slug in assigned:
            rows.append([
                InlineKeyboardButton(
                    text=info["name"],
                    callback_data=f"{slug}|{product_callback}",
                )
            ])
    rows.append([InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="city:tbilisi")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_confirmation_kb(
    price_usd: float, product_cb: str, district_cb: str, lang: str = "ka",
) -> InlineKeyboardMarkup:
    """Order confirmation with crypto payment buttons."""
    rates = texts.t_rates(lang)
    rows = []
    for crypto, info in rates.items():
        amount = round(price_usd * info["rate"], 6)
        # Encode: pay:BTC|product:prada_2|district:moskovis
        rows.append([
            InlineKeyboardButton(
                text=f"{info['label']}: {amount}",
                callback_data=f"pay:{crypto}|{product_cb}|{district_cb}",
            )
        ])
    rows.append([InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="city:tbilisi")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_back_kb(district_cb: str, product_cb: str, lang: str = "ka") -> InlineKeyboardMarkup:
    """Back button from payment detail screen -> order confirmation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=texts.t("BACK_BUTTON", lang),
                callback_data=f"{district_cb}|{product_cb}",
            )],
        ]
    )


def back_to_listing_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="city:tbilisi")],
        ]
    )


def back_only_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:main")],
        ]
    )


# ── Balance ───────────────────────────────────────────────────────────

def balance_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    """Balance screen with crypto deposit options."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Bitcoin", callback_data="deposit:BTC"),
            InlineKeyboardButton(text="Litecoin", callback_data="deposit:LTC"),
        ],
        [InlineKeyboardButton(text="USDT TRC20", callback_data="deposit:USDT")],
        [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:main")],
    ])


def deposit_back_kb(crypto: str, lang: str = "ka") -> InlineKeyboardMarkup:
    """Back from deposit detail + top-up again button."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.DEPOSIT_TOPUP_BUTTON, callback_data=f"deposit:{crypto}")],
        [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:balance")],
    ])


# ── Purchases ─────────────────────────────────────────────────────────

def purchases_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:main")],
    ])


# ── Referral ──────────────────────────────────────────────────────────

def referral_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:main")],
    ])


# ── Work ──────────────────────────────────────────────────────────────

def work_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:main")],
    ])


# ── Groups & Channels ────────────────────────────────────────────────

def groups_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:main")],
    ])


# ── Language ─────────────────────────────────────────────────────────

def language_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.LANGUAGE_KA_BUTTON, callback_data="lang:ka")],
        [InlineKeyboardButton(text=texts.LANGUAGE_RU_BUTTON, callback_data="lang:ru")],
        [InlineKeyboardButton(text=texts.LANGUAGE_EN_BUTTON, callback_data="lang:en")],
        [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:main")],
    ])


# ── Reserve Bots ─────────────────────────────────────────────────────

def reserve_bots_kb(lang: str = "ka") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.t("BACK_BUTTON", lang), callback_data="menu:main")],
    ])
