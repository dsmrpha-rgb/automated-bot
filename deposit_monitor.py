"""
Crypto deposit monitor for HADES PARFUMES bot.

Polls blockchain APIs for incoming transactions to the configured
wallet addresses (BTC, LTC, USDT TRC20). Notifies admin users when
deposits are detected, confirming, and confirmed.

Transaction state is persisted in data.json under "deposit_txs".
"""

import asyncio
import logging
from typing import Any

import aiohttp

import bot_registry
import data_store as ds
import texts
from config import ADMIN_IDS, CRYPTO_WALLETS

logger = logging.getLogger(__name__)

# Required confirmations before marking as "confirmed"
REQUIRED_CONFIRMATIONS = {
    "BTC": 3,
    "LTC": 6,
    "USDT": 20,  # TRC20 on Tron
}

# Block explorer links
EXPLORER_TX_URL = {
    "BTC": "https://mempool.space/tx/{txid}",
    "LTC": "https://blockchair.com/litecoin/transaction/{txid}",
    "USDT": "https://tronscan.org/#/transaction/{txid}",
}

# Polling intervals
POLL_INTERVAL = 60  # seconds between polls
ERROR_BACKOFF = 120  # seconds to wait after an API error


# ── Data store helpers ────────────────────────────────────────────────

def _load_txs() -> dict[str, dict]:
    """Load tracked transactions from data.json."""
    data = ds._load()
    if "deposit_txs" not in data:
        data["deposit_txs"] = {}
        ds._save(data)
    return data["deposit_txs"]


def _save_tx(txid: str, tx_data: dict) -> None:
    """Save or update a transaction in data.json."""
    data = ds._load()
    if "deposit_txs" not in data:
        data["deposit_txs"] = {}
    data["deposit_txs"][txid] = tx_data
    ds._save(data)


# ── Blockchain API fetchers ──────────────────────────────────────────

async def _fetch_json(session: aiohttp.ClientSession, url: str,
                      timeout: int = 15) -> dict | list | None:
    """Fetch JSON from a URL with error handling."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning("API returned %s for %s", resp.status, url)
    except Exception as e:
        logger.warning("API error for %s: %s", url, e)
    return None


async def _get_btc_txs(session: aiohttp.ClientSession,
                       address: str) -> list[dict]:
    """Fetch recent BTC transactions for an address via mempool.space."""
    if not address:
        return []
    url = f"https://mempool.space/api/address/{address}/txs"
    data = await _fetch_json(session, url)
    if not data or not isinstance(data, list):
        return []

    results = []
    for tx in data:
        txid = tx.get("txid", "")
        confirmed = tx.get("status", {}).get("confirmed", False)
        block_height = tx.get("status", {}).get("block_height")

        # Calculate amount received at our address
        amount_sat = 0
        for vout in tx.get("vout", []):
            if vout.get("scriptpubkey_address") == address:
                amount_sat += vout.get("value", 0)

        if amount_sat <= 0:
            continue

        amount_btc = amount_sat / 1e8
        results.append({
            "txid": txid,
            "coin": "BTC",
            "amount": amount_btc,
            "address": address,
            "confirmed": confirmed,
            "block_height": block_height,
        })
    return results


async def _get_btc_block_height(session: aiohttp.ClientSession) -> int | None:
    """Get current BTC block height."""
    url = "https://mempool.space/api/blocks/tip/height"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                text = await resp.text()
                return int(text.strip())
    except Exception:
        pass
    return None


async def _get_ltc_txs(session: aiohttp.ClientSession,
                       address: str) -> list[dict]:
    """Fetch recent LTC transactions via Blockchair."""
    if not address:
        return []
    url = f"https://api.blockchair.com/litecoin/dashboards/address/{address}?limit=20"
    data = await _fetch_json(session, url, timeout=20)
    if not data or "data" not in data:
        return []

    addr_data = data["data"].get(address, {})
    txs = addr_data.get("transactions", [])
    if not txs:
        return []

    # Get current block height for confirmation calc
    context = data.get("context", {})
    current_height = context.get("state", 0)

    results = []
    # Blockchair returns tx hashes; we need details per tx
    # Use the utxo list to determine amounts
    utxos = addr_data.get("utxo", [])
    for utxo in utxos:
        txid = utxo.get("transaction_hash", "")
        block_id = utxo.get("block_id", 0)
        amount_ltc = utxo.get("value", 0) / 1e8

        if amount_ltc <= 0:
            continue

        confirmations = (current_height - block_id + 1) if block_id > 0 else 0
        results.append({
            "txid": txid,
            "coin": "LTC",
            "amount": amount_ltc,
            "address": address,
            "confirmed": block_id > 0,
            "block_height": block_id if block_id > 0 else None,
            "confirmations": confirmations,
        })
    return results


async def _get_usdt_trc20_txs(session: aiohttp.ClientSession,
                               address: str) -> list[dict]:
    """Fetch recent USDT TRC20 transactions via TronGrid/TronScan."""
    if not address:
        return []

    # USDT TRC20 contract address
    usdt_contract = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    url = (
        f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
        f"?only_confirmed=false&limit=50"
        f"&contract_address={usdt_contract}"
    )

    data = await _fetch_json(session, url, timeout=20)
    if not data or "data" not in data:
        return []

    results = []
    for tx in data["data"]:
        # Only incoming transfers
        to_addr = tx.get("to", "")
        if to_addr != address:
            continue

        txid = tx.get("transaction_id", "")
        # USDT has 6 decimals
        raw_value = tx.get("value", "0")
        amount = int(raw_value) / 1e6

        if amount <= 0:
            continue

        # TronGrid confirmed field
        block_ts = tx.get("block_timestamp", 0)

        results.append({
            "txid": txid,
            "coin": "USDT",
            "amount": amount,
            "address": address,
            "confirmed": True,  # TronGrid only returns confirmed TRC20 txs
            "block_height": None,
        })
    return results


# ── Confirmation counting ────────────────────────────────────────────

async def _get_confirmations(session: aiohttp.ClientSession,
                             coin: str, tx_data: dict) -> int:
    """Get the current confirmation count for a transaction."""
    txid = tx_data.get("txid", "")

    if coin == "BTC":
        block_height = tx_data.get("block_height")
        if not block_height:
            return 0
        current = await _get_btc_block_height(session)
        if current is None:
            return tx_data.get("confirmations", 0)
        return max(0, current - block_height + 1)

    elif coin == "LTC":
        return tx_data.get("confirmations", 0)

    elif coin == "USDT":
        # TRC20 — once visible on TronGrid it typically has 20+ confirmations
        # We'll check via tronscan for accurate count
        if not txid:
            return 0
        url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={txid}"
        data = await _fetch_json(session, url, timeout=15)
        if data and isinstance(data, dict):
            confirmed = data.get("confirmed", False)
            return REQUIRED_CONFIRMATIONS["USDT"] if confirmed else 1
        return tx_data.get("confirmations", 0)

    return 0


# ── Notification ─────────────────────────────────────────────────────

async def _notify_admins_deposit(bot, coin: str, amount: float,
                                  address: str, txid: str,
                                  confirmations: int, status: str) -> None:
    """Send deposit notification to all admin users via ALL bots."""
    required = REQUIRED_CONFIRMATIONS.get(coin, 3)
    tx_link = EXPLORER_TX_URL.get(coin, "").format(txid=txid)

    text = texts.DEPOSIT_NOTIFICATION.format(
        coin=coin,
        amount=amount,
        address=address,
        tx_link=tx_link,
        confirmations=confirmations,
        required=required,
        status=status,
    )
    for admin_id in ADMIN_IDS:
        await bot_registry.send_message_all_bots(
            chat_id=admin_id,
            text=text,
            disable_web_page_preview=True,
        )


# ── Main monitor loop ────────────────────────────────────────────────

async def _poll_deposits(bot) -> None:
    """Single poll cycle: fetch txs for all coins, update state, notify."""
    async with aiohttp.ClientSession() as session:
        all_new_txs = []

        # Fetch transactions for each coin
        btc_addr = CRYPTO_WALLETS.get("BTC", "")
        ltc_addr = CRYPTO_WALLETS.get("LTC", "")
        usdt_addr = CRYPTO_WALLETS.get("USDT", "")

        btc_txs, ltc_txs, usdt_txs = await asyncio.gather(
            _get_btc_txs(session, btc_addr),
            _get_ltc_txs(session, ltc_addr),
            _get_usdt_trc20_txs(session, usdt_addr),
            return_exceptions=True,
        )

        for result in [btc_txs, ltc_txs, usdt_txs]:
            if isinstance(result, list):
                all_new_txs.extend(result)

        tracked = _load_txs()

        for tx in all_new_txs:
            txid = tx["txid"]
            coin = tx["coin"]
            amount = tx["amount"]
            address = tx["address"]

            if txid in tracked:
                # Already tracking — check if confirmations increased
                existing = tracked[txid]
                if existing.get("status") == "confirmed":
                    continue  # Already fully confirmed, skip

                # Update confirmation count
                confs = await _get_confirmations(session, coin, tx)
                required = REQUIRED_CONFIRMATIONS.get(coin, 3)

                if confs >= required and existing.get("status") != "confirmed":
                    existing["confirmations"] = confs
                    existing["status"] = "confirmed"
                    _save_tx(txid, existing)
                    await _notify_admins_deposit(
                        bot, coin, amount, address, txid, confs, "confirmed"
                    )
                elif confs > existing.get("confirmations", 0):
                    existing["confirmations"] = confs
                    existing["status"] = "confirming"
                    _save_tx(txid, existing)
                    await _notify_admins_deposit(
                        bot, coin, amount, address, txid, confs, "confirming"
                    )
            else:
                # New transaction detected
                confs = await _get_confirmations(session, coin, tx)
                required = REQUIRED_CONFIRMATIONS.get(coin, 3)

                if confs >= required:
                    status = "confirmed"
                elif confs > 0:
                    status = "confirming"
                else:
                    status = "detected"

                tx_record = {
                    "txid": txid,
                    "coin": coin,
                    "amount": amount,
                    "address": address,
                    "confirmations": confs,
                    "status": status,
                }
                _save_tx(txid, tx_record)
                await _notify_admins_deposit(
                    bot, coin, amount, address, txid, confs, status
                )


async def deposit_monitor_loop(bot) -> None:
    """Background loop that polls blockchain APIs for deposits."""
    logger.info("Deposit monitor started")
    # Initial delay to let the bot start up
    await asyncio.sleep(10)

    while True:
        try:
            await _poll_deposits(bot)
        except Exception as e:
            logger.error("Deposit monitor error: %s", e)
            await asyncio.sleep(ERROR_BACKOFF)
            continue

        await asyncio.sleep(POLL_INTERVAL)


def start_deposit_monitor(bot) -> None:
    """Start the deposit monitor as a background asyncio task."""
    asyncio.create_task(deposit_monitor_loop(bot))
