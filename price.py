"""
Free, keyless price + market cap lookup via DexScreener's public API. Used at
trade ingest time to turn a raw token quantity into an actual USD value, and
to capture the market cap at the moment each trade happened — without this,
"marketcap bought" / "marketcap sold" would just be guesses.

Robinhood Chain isn't indexed by DexScreener yet (launched July 1 2026),
so lookups there return (None, None) until that changes.
"""
import httpx

_DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"

_SUPPORTED_CHAIN_IDS = {
    "ethereum": "ethereum",
    "base": "base",
    "solana": "solana",
    "robinhood": None,  # not indexed yet
}


async def fetch_token_market_data(chain: str, token_address: str) -> tuple[float | None, float | None]:
    """Returns (price_usd, market_cap_usd). Either can be None if unavailable —
    callers should treat None as 'unknown', never coerce to 0."""
    chain_id = _SUPPORTED_CHAIN_IDS.get(chain)
    if not chain_id:
        return None, None

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(_DEXSCREENER_TOKEN_URL.format(address=token_address))
            if resp.status_code != 200:
                return None, None
            data = resp.json()
    except httpx.HTTPError:
        return None, None

    pairs = data.get("pairs") or []
    matching = [p for p in pairs if p.get("chainId") == chain_id and p.get("priceUsd")]
    if not matching:
        return None, None

    # Most-liquid pair is the most reliable price/mcap read, least likely to be a thin/dead pair
    best = max(matching, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))

    try:
        price = float(best["priceUsd"])
    except (TypeError, ValueError):
        price = None

    # marketCap preferred; fdv (fully diluted valuation) as fallback if mcap isn't reported
    mcap_raw = best.get("marketCap") or best.get("fdv")
    try:
        market_cap = float(mcap_raw) if mcap_raw is not None else None
    except (TypeError, ValueError):
        market_cap = None

    return price, market_cap


async def fetch_token_price_usd(chain: str, token_address: str) -> float | None:
    """Back-compat wrapper for callers that only need price."""
    price, _ = await fetch_token_market_data(chain, token_address)
    return price
