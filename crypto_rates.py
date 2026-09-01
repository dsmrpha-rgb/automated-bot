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


async def get_crypto_price_usd(coin: str) -> float:
    """
    Get the current USD price of 1 unit of the given crypto.
    Returns cached value if fresh enough.
    """
    coin = coin.upper()

    # USDT is a stablecoin — always 1:1
    if coin == "USDT":
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
    coin_ids = {
        "BTC": "bitcoin",
        "LTC": "litecoin",
    }
    cg_id = coin_ids.get(coin)
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
    if coin == "USDT":
        return round(usd_amount, 2)  # 1:1, no conversion needed

    price_usd = await get_crypto_price_usd(coin)
    if price_usd <= 0:
        return 0.0
    return round(usd_amount / price_usd, 8)


async def get_rates_for_display(usd_amount: float) -> dict[str, dict]:
    """
    Get all crypto rates formatted for display in the order confirmation.
    Returns: {"BTC": {"amount": 0.000928, "label": "..."}, ...}
    """
    results = {}
    for coin in ["BTC", "LTC", "USDT"]:
        amount = await get_usd_to_crypto(coin, usd_amount)
        labels = {
            "BTC": "BTC გადახდისთვის",
            "LTC": "LTC გადახდისთვის",
            "USDT": "USDT TRC20 გადახდისთვის",
        }
        results[coin] = {
            "amount": amount,
            "label": labels[coin],
        }
    return results
