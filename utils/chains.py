"""
One place to build the "helpful information" links that go in every trade
notification — contract explorer + chart/liquidity view. Chain-aware since
each ecosystem has its own tools.
"""

_EXPLORER_TOKEN_URL = {
    "ethereum": "https://etherscan.io/token/{address}",
    "base": "https://basescan.org/token/{address}",
    "solana": "https://solscan.io/token/{address}",
    # Robinhood Chain launched July 1 2026 — block explorer exists but isn't
    # stable/public enough yet to hardlink reliably. Falls back to None below.
    "robinhood": None,
}

# DexScreener chain slugs — used for both the chart link and confirming a pair exists
_DEXSCREENER_CHAIN_SLUG = {
    "ethereum": "ethereum",
    "base": "base",
    "solana": "solana",
    "robinhood": None,  # not indexed yet as of launch
}


def explorer_url(chain: str, token_address: str) -> str | None:
    template = _EXPLORER_TOKEN_URL.get(chain)
    return template.format(address=token_address) if template else None


def dexscreener_url(chain: str, token_address: str) -> str | None:
    slug = _DEXSCREENER_CHAIN_SLUG.get(chain)
    return f"https://dexscreener.com/{slug}/{token_address}" if slug else None


def wallet_explorer_url(chain: str, wallet_address: str) -> str | None:
    if chain == "solana":
        return f"https://solscan.io/account/{wallet_address}"
    if chain == "ethereum":
        return f"https://etherscan.io/address/{wallet_address}"
    if chain == "base":
        return f"https://basescan.org/address/{wallet_address}"
    return None


def helpful_links(chain: str, token_address: str) -> str:
    """Returns a formatted string of whatever links are available for this chain."""
    links = []
    dex = dexscreener_url(chain, token_address)
    if dex:
        links.append(f"[Chart]({dex})")
    exp = explorer_url(chain, token_address)
    if exp:
        links.append(f"[Explorer]({exp})")
    return " | ".join(links) if links else "_no indexer links yet for this chain_"
