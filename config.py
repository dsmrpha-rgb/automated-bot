import os

from dotenv import load_dotenv

load_dotenv()

# Support multiple bot tokens: BOT_TOKENS=token1,token2,token3
# Also accepts single BOT_TOKEN for backward compatibility
_tokens_raw = os.environ.get("BOT_TOKENS", "")
_single_token = os.environ.get("BOT_TOKEN", "")
if _tokens_raw.strip():
    BOT_TOKENS: list[str] = [t.strip() for t in _tokens_raw.split(",") if t.strip()]
elif _single_token.strip():
    BOT_TOKENS = [_single_token.strip()]
else:
    raise RuntimeError("Set BOT_TOKENS or BOT_TOKEN in .env")

# Keep BOT_TOKEN for backward compat (first token)
BOT_TOKEN = BOT_TOKENS[0]

# Admin user IDs (comma-separated in .env, e.g. ADMIN_IDS=123456,789012)
_admin_raw = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()]

# Crypto wallet addresses
BTC_WALLET = os.environ.get("BTC_WALLET", "")
LTC_WALLET = os.environ.get("LTC_WALLET", "")
USDT_WALLET = os.environ.get("USDT_WALLET", "")

CRYPTO_WALLETS = {
    "BTC": BTC_WALLET,
    "LTC": LTC_WALLET,
    "USDT": USDT_WALLET,
}

_BASE_DIR = os.path.dirname(__file__)

MENU_IMAGE_PATH = os.path.join(_BASE_DIR, "menu.jpg")
LISTING_IMAGE_PATH = os.path.join(_BASE_DIR, "listing.jpg")
MOSKOVIS_IMAGE_PATH = os.path.join(_BASE_DIR, "moskovisgamz.jpg")
BOUGHT_IMAGE_PATH = os.path.join(_BASE_DIR, "bought.jpg")
VAKANSIA_IMAGE_PATH = os.path.join(_BASE_DIR, "vakansia.jpg")
GROUPS_IMAGE_PATH = os.path.join(_BASE_DIR, "groups.jpg")
LOGO_PATH = os.path.join(_BASE_DIR, "hades_logo.png")


def district_image_path(filename: str) -> str:
    """Return the full path for a district image file.

    Falls back to LISTING_IMAGE_PATH when no image is set.
    """
    if not filename:
        return LISTING_IMAGE_PATH
    return os.path.join(_BASE_DIR, filename)
