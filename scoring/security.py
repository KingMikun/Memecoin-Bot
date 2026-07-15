"""
The gate. Nothing reaches Telegram without clearing this first.

Uses GoPlus Security's free Token Security API — covers 40+ chains,
returns honeypot flag, buy/sell tax, mint/freeze authority (Solana),
holder concentration, and contract ownership status.

Docs: https://docs.gopluslabs.io
"""
import httpx
from config import CHAINS, MAX_TOP10_HOLDER_PCT, MIN_LIQUIDITY_USD

GOPLUS_EVM_URL = "https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
GOPLUS_SOLANA_URL = "https://api.gopluslabs.io/api/v1/solana/token_security"


class SecurityResult:
    def __init__(self, passed: bool, reasons: list[str], raw: dict | None = None):
        self.passed = passed
        self.reasons = reasons
        self.raw = raw or {}

    def __repr__(self):
        return f"<SecurityResult passed={self.passed} reasons={self.reasons}>"


async def check_token(chain: str, token_address: str) -> SecurityResult:
    """Run the honeypot / rug gate for a token on a given chain."""
    chain_cfg = CHAINS.get(chain)
    if not chain_cfg:
        return SecurityResult(False, [f"unsupported chain: {chain}"])

    if chain_cfg["kind"] == "solana":
        return await _check_solana(token_address)

    if chain_cfg["goplus_id"] is None:
        # Robinhood Chain: GoPlus hasn't assigned a chain ID yet as of launch.
        # Falls back to a manual "unverified — proceed with caution" flag rather
        # than a false pass. Swap this out the moment GoPlus adds coverage.
        return SecurityResult(
            False,
            ["Robinhood Chain not yet covered by GoPlus — manual review required"],
        )

    return await _check_evm(chain_cfg["goplus_id"], token_address)


async def _check_evm(goplus_chain_id: str, token_address: str) -> SecurityResult:
    url = GOPLUS_EVM_URL.format(chain_id=goplus_chain_id)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params={"contract_addresses": token_address})
        resp.raise_for_status()
        data = resp.json().get("result", {}).get(token_address.lower(), {})

    reasons = []
    if data.get("is_honeypot") == "1":
        reasons.append("flagged as honeypot")
    if data.get("cannot_sell_all") == "1":
        reasons.append("cannot sell full balance")
    if data.get("is_open_source") == "0":
        reasons.append("contract not verified/open-source")
    sell_tax = float(data.get("sell_tax") or 0)
    if sell_tax > 0.15:
        reasons.append(f"sell tax {sell_tax*100:.0f}% — predatory")
    if data.get("is_mintable") == "1":
        reasons.append("mint function still live")
    holders = data.get("holders", [])
    if holders:
        top10_pct = sum(float(h.get("percent", 0)) for h in holders[:10]) * 100
        if top10_pct > MAX_TOP10_HOLDER_PCT:
            reasons.append(f"top 10 holders control {top10_pct:.0f}% of supply")

    return SecurityResult(passed=len(reasons) == 0, reasons=reasons, raw=data)


async def _check_solana(token_address: str) -> SecurityResult:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(GOPLUS_SOLANA_URL, params={"contract_addresses": token_address})
        resp.raise_for_status()
        data = resp.json().get("result", {}).get(token_address, {})

    reasons = []
    if data.get("mintable", {}).get("status") == "1":
        reasons.append("mint authority not renounced")
    if data.get("freezable", {}).get("status") == "1":
        reasons.append("freeze authority active — can lock your wallet's tokens")
    if data.get("balance_mutable_authority", {}).get("status") == "1":
        reasons.append("balance can be mutated by an authority")

    return SecurityResult(passed=len(reasons) == 0, reasons=reasons, raw=data)
