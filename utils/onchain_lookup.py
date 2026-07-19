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

import httpx

from config import HELIUS_API_KEY, ALCHEMY_API_KEY
from ingest.helius_webhook import _extract_swap

logger = logging.getLogger(__name__)

HELIUS_HISTORY_URL = "https://api.helius.xyz/v0/addresses/{address}/transactions"

# Alchemy network subdomains for the JSON-RPC endpoint. Robinhood Chain isn't
# listed because, as of writing, Alchemy hasn't published a confirmed
# subdomain for it — preview falls back to "not available yet" on that chain
# rather than guessing a URL and silently returning nothing.
_ALCHEMY_NETWORK_SUBDOMAIN = {
    "ethereum": "eth-mainnet",
    "base": "base-mainnet",
}


class PreviewTrade:
    def __init__(self, action, token_address, token_symbol, token_amount, tx_hash, chain):
        self.action = action
        self.token_address = token_address
        self.token_symbol = token_symbol
        self.token_amount = token_amount
        self.tx_hash = tx_hash
        self.chain = chain


async def fetch_solana_preview(address: str, limit: int = 10) -> list[PreviewTrade] | None:
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


async def fetch_evm_preview(address: str, chain: str, limit: int = 10) -> list[PreviewTrade] | None:
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


async def fetch_evm_preview_all_chains(address: str, chains: list[str], limit: int = 10) -> list[PreviewTrade] | None:
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

    combined: list[PreviewTrade] = []
    for r in results:
        if r:
            combined.extend(r)
    logger.info(f"[onchain_lookup] Combined EVM preview for {address} across {chains}: {len(combined)} total trade(s)")
    return combined[:limit]


def format_preview(trades: list[PreviewTrade] | None, chain_label: str, address: str) -> str:
    if trades is None:
        return (
            f"No preview available right now — this needs "
            f"{'HELIUS_API_KEY' if chain_label == 'solana' else 'ALCHEMY_API_KEY'} configured, "
            f"or this chain isn't supported for live lookups yet.\n\n"
            f"Track it with /addwallet to start collecting trades from here on, "
            f"which does work regardless."
        )
    if not trades:
        return f"No recent swap activity found for this wallet on {chain_label}."

    lines = [f"*Live preview* (not tracked) — {chain_label}\n"]
    for t in trades[:10]:
        emoji = "🟢" if t.action == "buy" else "🔴"
        chain_tag = f" [{t.chain.title()}]" if chain_label == "EVM (Ethereum/Base/Robinhood)" else ""
        lines.append(f"{emoji} {t.action.upper()} `{t.token_symbol or t.token_address[:8]}`{chain_tag} — {t.token_amount:,.4g}")

    lines.append(
        "\n_This is raw on-chain activity, not FIFO-matched PnL — win rate/PnL "
        "need trades collected over time. /addwallet this address to start tracking it properly._"
    )
    return "\n".join(lines)
