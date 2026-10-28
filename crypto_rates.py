"""
Live crypto rate fetcher for HADES PARFUMES bot.

Fetches BTC, LTC prices from CoinGecko free API.
USDT is always treated as 1:1 with USD (stablecoin).
Caches rates for 5 minutes to avoid hitting API limits.
"""

import asyncio
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# Cache: {coin: (rate_usd_per_1_coin, timestamp)}
_cache: dict[str, tuple[float, float]] = {}
CACHE_TTL = 300  # 5 minutes

# Fallback rates (used only if API is completely unreachable)
_FALLBACK_RATES = {
    "BTC": 70000.0,
    "LTC": 80.0,
    "USDT": 1.0,
}


_BUILTIN_IDS = {
    "BTC": "bitcoin", "LTC": "litecoin", "ETH": "ethereum", "TRX": "tron",
    "TON": "the-open-network", "SOL": "solana", "DOGE": "dogecoin",
    "XRP": "ripple", "BNB": "binancecoin", "BCH": "bitcoin-cash",
}
_BUILTIN_STABLE = {"USDT", "USDC", "DAI", "BUSD"}


def _coin_ids() -> dict[str, str]:
    import settings_store
    ids = dict(_BUILTIN_IDS)
    for c in settings_store.coins():
        if c.get("cg_id"):
            ids[c["code"]] = c["cg_id"]
    return ids


def _is_stable(coin: str) -> bool:
    import settings_store
    if coin in _BUILTIN_STABLE:
        return True
    return any(c["code"] == coin and c.get("stable") for c in settings_store.coins())


def _custom_label(code: str) -> bool:
    import settings_store
    defaults = {c["code"]: c["label"] for c in settings_store.DEFAULT_COINS}
    return any(c["code"] == code and c["label"] != defaults.get(code) for c in settings_store.coins())


async def get_crypto_price_usd(coin: str) -> float:
    """
    Get the current USD price of 1 unit of the given crypto.
    Returns cached value if fresh enough.
    """
    coin = coin.upper()

    # stablecoins are always 1:1
    if _is_stable(coin):
        return 1.0

    # Check cache
    if coin in _cache:
        price, ts = _cache[coin]
        if time.time() - ts < CACHE_TTL:
            return price

    # Fetch from CoinGecko
    price = await _fetch_price_coingecko(coin)
    if price and price > 0:
        _cache[coin] = (price, time.time())
        return price

    # If cache exists but expired, use stale cache rather than fallback
    if coin in _cache:
        logger.warning("API failed for %s, using stale cached rate", coin)
        return _cache[coin][0]

    # Last resort: fallback
    logger.warning("Using fallback rate for %s", coin)
    return _FALLBACK_RATES.get(coin, 1.0)


async def _fetch_price_coingecko(coin: str) -> Optional[float]:
    """Fetch price from CoinGecko free API."""
    cg_id = _coin_ids().get(coin)
    if not cg_id:
        return None

    url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = data.get(cg_id, {}).get("usd")
                    if price:
                        logger.info("Fetched %s price: $%.2f", coin, price)
                        return float(price)
                else:
                    logger.warning("CoinGecko returned %s for %s", resp.status, coin)
    except Exception as e:
        logger.warning("CoinGecko error for %s: %s", coin, e)
    return None


async def get_usd_to_crypto(coin: str, usd_amount: float) -> float:
    """
    Convert a USD amount to the equivalent crypto amount.
    Example: get_usd_to_crypto("BTC", 65) -> 0.000928 (if BTC = $70,000)
    """
    coin = coin.upper()
    if _is_stable(coin):
        return round(usd_amount, 2)  # 1:1, no conversion needed

    price_usd = await get_crypto_price_usd(coin)
    if price_usd <= 0:
        return 0.0
    return round(usd_amount / price_usd, 8)


async def get_rates_for_display(usd_amount: float) -> dict[str, dict]:
    """
    Get all crypto rates formatted for display in the order confirmation.
    Returns: {"BTC": {"amount": 0.000928, "label": "..."}, ...}
    Coins and labels come from the constructor (payments.coins).
    """
    import settings_store
    results = {}
    for c in settings_store.coins():
        amount = await get_usd_to_crypto(c["code"], usd_amount)
        results[c["code"]] = {
            "amount": amount,
            "label": f"{c['label']} გადახდისთვის" if c["code"] in ("BTC", "LTC", "USDT") and not _custom_label(c["code"]) else c["label"],
        }
    return results
