"""
Closes the gap between "bot knows about a wallet" and "Helius/Alchemy are
actually watching it." One call each way, add or remove.

Helius: webhooks store a flat accountAddresses list — you GET the current
list, splice the address in/out, PUT the whole thing back. No partial-update
endpoint exists, so this always round-trips.

Alchemy: the Notify API has a proper partial-update endpoint
(addresses_to_add / addresses_to_remove), no round-trip needed.
"""
import httpx

from config import (
    HELIUS_API_KEY, HELIUS_WEBHOOK_ID,
    ALCHEMY_AUTH_TOKEN, ALCHEMY_WEBHOOK_IDS,
)

HELIUS_WEBHOOK_URL = f"https://api.helius.xyz/v0/webhooks/{{webhook_id}}?api-key={HELIUS_API_KEY}"
ALCHEMY_UPDATE_URL = "https://dashboard.alchemy.com/api/update-webhook-addresses"


class SyncResult:
    def __init__(self, ok: bool, message: str):
        self.ok = ok
        self.message = message


async def sync_solana_address(address: str, action: str = "add") -> SyncResult:
    if not HELIUS_API_KEY or not HELIUS_WEBHOOK_ID:
        return SyncResult(False, "Helius not configured — set HELIUS_API_KEY and HELIUS_WEBHOOK_ID")

    url = HELIUS_WEBHOOK_URL.format(webhook_id=HELIUS_WEBHOOK_ID)

    async with httpx.AsyncClient(timeout=15) as client:
        get_resp = await client.get(url)
        if get_resp.status_code != 200:
            return SyncResult(False, f"Helius GET failed: {get_resp.status_code} {get_resp.text[:200]}")

        webhook = get_resp.json()
        current = set(webhook.get("accountAddresses", []))

        if action == "add":
            if address in current:
                return SyncResult(True, "already on the Helius webhook")
            current.add(address)
        else:
            current.discard(address)

        payload = {**webhook, "accountAddresses": list(current)}
        put_resp = await client.put(url, json=payload)

        if put_resp.status_code != 200:
            return SyncResult(False, f"Helius PUT failed: {put_resp.status_code} {put_resp.text[:200]}")

    return SyncResult(True, f"{'added to' if action == 'add' else 'removed from'} Helius webhook")


async def sync_evm_address(address: str, chain: str, action: str = "add") -> SyncResult:
    webhook_id = ALCHEMY_WEBHOOK_IDS.get(chain, "")
    if not ALCHEMY_AUTH_TOKEN or not webhook_id:
        return SyncResult(
            False,
            f"Alchemy not configured for {chain} — set ALCHEMY_AUTH_TOKEN and "
            f"ALCHEMY_WEBHOOK_ID_{chain.upper()}",
        )

    body = {"webhook_id": webhook_id}
    if action == "add":
        body["addresses_to_add"] = [address]
        body["addresses_to_remove"] = []
    else:
        body["addresses_to_add"] = []
        body["addresses_to_remove"] = [address]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.patch(
            ALCHEMY_UPDATE_URL,
            headers={"X-Alchemy-Token": ALCHEMY_AUTH_TOKEN, "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code not in (200, 201):
            return SyncResult(False, f"Alchemy update failed: {resp.status_code} {resp.text[:200]}")

    return SyncResult(True, f"{'added to' if action == 'add' else 'removed from'} Alchemy webhook ({chain})")


async def sync_wallet(address: str, chain: str, kind: str, action: str = "add") -> SyncResult:
    """Single entrypoint — routes to the right provider based on chain kind."""
    if kind == "solana":
        return await sync_solana_address(address, action=action)
    return await sync_evm_address(address, chain, action=action)
