"""
Preview mode: pull a wallet's recent trade history directly from the chain,
on demand, for any address — no /addwallet required first.

This is deliberately separate from the tracked-wallet pipeline (ingest/*_webhook.py).
Those only ever see trades that happen *after* a wallet is registered with
Helius/Alchemy. This module answers a different question: "what has this
wallet done recently, right now, before I decide whether to track it."

Solana reuses the exact same SWAP-parsing logic as the live webhook
(ingest.helius_webhook._extract_swap) so a previewed trade and a live-tracked
trade are parsed identically — one source of truth, not two.
"""
import logging
import uuid

import httpx

from config import HELIUS_API_KEY, ALCHEMY_API_KEY
from ingest.helius_webhook import _extract_swap
from utils.price import fetch_token_market_data

logger = logging.getLogger(__name__)

HELIUS_HISTORY_URL = "https://api.helius.xyz/v0/addresses/{address}/transactions"
DEFAULT_PREVIEW_LIMIT = 50
PAGE_SIZE = 10

# Alchemy network subdomains for the JSON-RPC endpoint. Robinhood Chain
# mainnet is confirmed live on Alchemy (chain ID 4663) with Transfers API
# support, so it's wired in the same way as Ethereum/Base — no more silent
# "unsupported" fallback for this chain.
_ALCHEMY_NETWORK_SUBDOMAIN = {
    "ethereum": "eth-mainnet",
    "base": "base-mainnet",
    "robinhood": "robinhood-mainnet",
}


class PreviewTrade:
    def __init__(self, action, token_address, token_symbol, token_amount, tx_hash, chain):
        self.action = action
        self.token_address = token_address
        self.token_symbol = token_symbol
        self.token_amount = token_amount
        self.tx_hash = tx_hash
        self.chain = chain
        self.current_market_cap: float | None = None  # filled in by _enrich_with_current_mcap


async def fetch_solana_preview(address: str, limit: int = DEFAULT_PREVIEW_LIMIT) -> list[PreviewTrade] | None:
    """Returns recent swaps for a Solana address, or None if the lookup itself failed
    (missing API key, network error) — distinct from an empty list, which means the
    lookup succeeded but the wallet genuinely has no recent swap activity."""
    if not HELIUS_API_KEY:
        logger.warning("[onchain_lookup] Solana preview requested but HELIUS_API_KEY is not set")
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                HELIUS_HISTORY_URL.format(address=address),
                params={"api-key": HELIUS_API_KEY, "type": "SWAP", "limit": limit},
            )
            if resp.status_code != 200:
                logger.warning(
                    f"[onchain_lookup] Helius history lookup failed for {address}: "
                    f"HTTP {resp.status_code} — {resp.text[:300]}"
                )
                return None
            events = resp.json()
    except httpx.HTTPError as e:
        logger.warning(f"[onchain_lookup] Helius history lookup network error for {address}: {e}")
        return None

    trades = []
    for event in events:
        swap = _extract_swap(event)
        if swap is None:
            continue
        _, token_address, token_symbol, action, token_amount, tx_hash = swap
        trades.append(PreviewTrade(action, token_address, token_symbol, token_amount, tx_hash, chain="solana"))
    logger.info(f"[onchain_lookup] Helius preview for {address}: {len(events)} raw event(s), {len(trades)} parsed as swaps")
    return trades


async def fetch_evm_preview(address: str, chain: str, limit: int = DEFAULT_PREVIEW_LIMIT) -> list[PreviewTrade] | None:
    """Returns recent ERC-20 transfers in/out of an EVM address, interpreted as
    buy/sell. Returns None if unavailable (missing key, unsupported chain, network error)."""
    subdomain = _ALCHEMY_NETWORK_SUBDOMAIN.get(chain)
    if not ALCHEMY_API_KEY:
        logger.warning("[onchain_lookup] EVM preview requested but ALCHEMY_API_KEY is not set")
        return None
    if not subdomain:
        logger.warning(f"[onchain_lookup] EVM preview requested for unsupported chain '{chain}' (no Alchemy subdomain mapped)")
        return None

    url = f"https://{subdomain}.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
    trades: list[PreviewTrade] = []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for direction, action in (("toAddress", "buy"), ("fromAddress", "sell")):
                resp = await client.post(url, json={
                    "jsonrpc": "2.0", "id": 1, "method": "alchemy_getAssetTransfers",
                    "params": [{
                        direction: address,
                        "category": ["erc20"],
                        "maxCount": hex(limit),
                        "order": "desc",
                    }],
                })
                if resp.status_code != 200:
                    logger.warning(
                        f"[onchain_lookup] Alchemy lookup failed for {address} ({action} direction): "
                        f"HTTP {resp.status_code} — {resp.text[:300]}"
                    )
                    continue
                body = resp.json()
                if "error" in body:
                    logger.warning(f"[onchain_lookup] Alchemy RPC error for {address} ({action} direction): {body['error']}")
                    continue
                transfers = body.get("result", {}).get("transfers", [])
                for t in transfers:
                    trades.append(PreviewTrade(
                        action=action,
                        token_address=(t.get("rawContract") or {}).get("address", ""),
                        token_symbol=t.get("asset", ""),
                        token_amount=float(t.get("value") or 0),
                        tx_hash=t.get("hash", ""),
                        chain=chain,
                    ))
    except httpx.HTTPError as e:
        logger.warning(f"[onchain_lookup] Alchemy lookup network error for {address}: {e}")
        return None

    trades.sort(key=lambda t: t.tx_hash)  # stable-ish grouping; exact order isn't critical for a preview
    logger.info(f"[onchain_lookup] Alchemy preview for {address} on {chain}: {len(trades)} transfer(s)")
    return trades[:limit]


async def fetch_evm_preview_all_chains(address: str, chains: list[str], limit: int = DEFAULT_PREVIEW_LIMIT) -> list[PreviewTrade] | None:
    """
    An EVM address is valid on every EVM chain — checking only one (as the
    original version of this function did, always defaulting to Ethereum)
    would silently miss activity on Base or Robinhood Chain, which is where
    most memecoin trading actually happens. Queries all given chains
    concurrently and merges the results, each trade labeled with its chain.

    Returns None only if EVERY chain's lookup failed outright (e.g. no API
    key at all) — a chain with zero activity still counts as a successful
    empty result, not a failure.
    """
    import asyncio
    results = await asyncio.gather(*(fetch_evm_preview(address, c, limit) for c in chains))

    if all(r is None for r in results):
        return None

    per_chain = [r for r in results if r]  # drop chains that failed/returned None, keep chains with (possibly empty) results

    # Interleave round-robin across chains rather than concatenate-then-slice.
    # A concatenate would let a high-volume chain (Ethereum, almost always)
    # fill the entire display before a lower-volume chain like Base or
    # Robinhood ever gets a slot — exactly the bug this replaces.
    combined: list[PreviewTrade] = []
    max_len = max((len(r) for r in per_chain), default=0)
    for i in range(max_len):
        for chain_trades in per_chain:
            if i < len(chain_trades):
                combined.append(chain_trades[i])

    logger.info(f"[onchain_lookup] Combined EVM preview for {address} across {chains}: "
                f"{len(combined)} total trade(s), per-chain counts: {[len(r) for r in results if r is not None]}")
    return combined[:limit]


async def _enrich_with_current_mcap(trades: list[PreviewTrade]) -> None:
    """
    Fills in current_market_cap for each trade — one lookup per unique
    (chain, token) pair, not per trade, to avoid redundant API calls when
    a wallet traded the same token multiple times.

    Deliberately does NOT attempt to estimate a historical mcap at the time
    of each past trade — we don't have a historical price feed wired up, and
    faking one off today's price would produce numbers that look precise but
    are actually wrong. This is "what's it worth now," not "what was it
    worth then." Real entry/exit mcap is only available for tracked wallets,
    where price is captured at the actual moment of each trade.
    """
    unique_tokens = {(t.chain, t.token_address) for t in trades if t.chain != "robinhood"}
    mcap_cache: dict[tuple[str, str], float | None] = {}
    for chain, token_address in unique_tokens:
        _, mcap = await fetch_token_market_data(chain, token_address)
        mcap_cache[(chain, token_address)] = mcap

    for t in trades:
        t.current_market_cap = mcap_cache.get((t.chain, t.token_address))


# In-memory session store so Next/Previous buttons can page through an
# already-fetched result set without re-hitting Helius/Alchemy on every
# click. Keyed by a short id (fits easily in Telegram's 64-byte callback_data
# limit) rather than the address itself. Resets on redeploy — fine, since
# this is just pagination state for an in-progress preview, not durable data.
_preview_sessions: dict[str, list[PreviewTrade]] = {}


def _store_preview_session(trades: list[PreviewTrade]) -> str:
    session_id = uuid.uuid4().hex[:10]
    _preview_sessions[session_id] = trades
    if len(_preview_sessions) > 500:  # simple unbounded-growth guard
        _preview_sessions.pop(next(iter(_preview_sessions)), None)
    return session_id


def get_preview_session(session_id: str) -> list[PreviewTrade] | None:
    return _preview_sessions.get(session_id)


def _format_trade_line(t: PreviewTrade, show_chain_tag: bool) -> str:
    emoji = "🟢" if t.action == "buy" else "🔴"
    chain_tag = f" [{t.chain.title()}]" if show_chain_tag else ""
    mcap = f" — mcap ${t.current_market_cap:,.0f}" if t.current_market_cap else ""
    return f"{emoji} {t.action.upper()} `{t.token_symbol or t.token_address[:8]}`{chain_tag} — {t.token_amount:,.4g}{mcap}"


def format_preview_page(trades: list[PreviewTrade], page: int, chain_label: str) -> tuple[str, int]:
    """Returns (message_text, total_pages) for one page of an already-fetched trade list."""
    if not trades:
        return f"No recent swap activity found for this wallet on {chain_label}.", 1

    total_pages = max(1, (len(trades) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start, end = page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE
    page_trades = trades[start:end]

    show_chain_tag = chain_label == "EVM (Ethereum/Base/Robinhood)"
    lines = [f"*Live preview* (not tracked) — {chain_label}", f"Page {page + 1}/{total_pages} — {len(trades)} trade(s) total\n"]
    lines += [_format_trade_line(t, show_chain_tag) for t in page_trades]
    lines.append(
        "\n_Raw on-chain activity with current market cap shown for context — not the "
        "price at the time of each trade, and not FIFO-matched PnL. Real win rate/PnL "
        "need trades collected over time. /addwallet this address to start tracking it properly._"
    )
    return "\n".join(lines), total_pages


def format_preview_unavailable(chain_label: str) -> str:
    return (
        f"No preview available right now — this needs "
        f"{'HELIUS_API_KEY' if chain_label == 'solana' else 'ALCHEMY_API_KEY'} configured, "
        f"or this chain isn't supported for live lookups yet.\n\n"
        f"Track it with /addwallet to start collecting trades from here on, "
        f"which does work regardless."
    )


def build_pagination_keyboard(session_id: str, page: int, total_pages: int):
    """Builds the Previous/Next inline keyboard. Returns None if there's only
    one page — no point showing dead buttons."""
    if total_pages <= 1:
        return None

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("◀ Previous", callback_data=f"pvpage:{session_id}:{page - 1}"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"pvpage:{session_id}:{page + 1}"))
    return InlineKeyboardMarkup([buttons])
