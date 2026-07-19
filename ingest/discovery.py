"""
Auto-pulls Solana's live leaderboard from kolscan.io — free, public, no key.
Filters out one-lucky-trade noise so you're not tracking a wallet that got
green off a single ape.

There is no free equivalent for Ethereum/Base. The real global "smart money"
leaderboard for EVM lives behind Nansen's paid API (docs.nansen.ai) — the
`seed_evm_from_birdeye_top_traders()` stub below is the closest free
alternative: pull Top Traders per-token from Birdeye for your highest
conviction plays, rather than pretending a clean global list exists.

Run manually:
    python -m ingest.discovery --min-trades 10 --top 15

Or wire it to a scheduled job (Railway cron, or an APScheduler job inside
main.py) to keep the bench fresh daily.
"""
import argparse
import re
import httpx
from sqlalchemy import select

from database import Wallet, get_session

KOLSCAN_LEADERBOARD_URL = "https://kolscan.io/leaderboard"

# Matches: [pfp <Label>](https://kolscan.io/account/<address>?timeframe=1)
# followed later by "<wins>\n\n/\n\n<losses>" and a "+<pnl> Sol" line.
# KOLscan is a Next.js app — if this stops matching, the page moved to a
# client-only render and this needs to switch to their internal API
# (check Network tab for an /api/leaderboard call) or a headless browser.
_ENTRY_RE = re.compile(
    r"\[pfp ([^\]]*)\]\(https://kolscan\.io/account/([1-9A-HJ-NP-Za-km-z]{32,44})[^\)]*\).*?"
    r"(\d+)\s*/\s*(\d+).*?\+([\d.]+)\s*Sol",
    re.DOTALL,
)


def parse_kolscan_html(markdown_text: str, min_trades: int = 5) -> list[dict]:
    """Parse KOLscan's rendered leaderboard markdown into wallet records."""
    results = []
    for label, address, wins, losses, pnl_sol in _ENTRY_RE.findall(markdown_text):
        wins, losses = int(wins), int(losses)
        total_trades = wins + losses
        if total_trades < min_trades:
            continue
        results.append({
            "label": f"KOLscan: {label.strip() or address[:6]}",
            "address": address,
            "chain": "solana",
            "wins": wins,
            "losses": losses,
            "pnl_sol": float(pnl_sol),
        })
    return results


def fetch_kolscan_leaderboard(min_trades: int = 5) -> list[dict]:
    resp = httpx.get(KOLSCAN_LEADERBOARD_URL, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    return parse_kolscan_html(resp.text, min_trades=min_trades)


def seed_from_kolscan(min_trades: int = 5, top: int = 15) -> tuple[int, int]:
    """Pulls the leaderboard and inserts new wallets, skipping duplicates."""
    entries = fetch_kolscan_leaderboard(min_trades=min_trades)[:top]

    session = get_session()
    added, skipped = 0, 0
    try:
        for e in entries:
            existing = session.execute(
                select(Wallet).where(Wallet.address == e["address"], Wallet.chain == "solana")
            ).scalar_one_or_none()
            if existing:
                skipped += 1
                continue
            wallet = Wallet(address=e["address"], chain="solana", label=e["label"])
            wallet.win_count = e["wins"]
            wallet.loss_count = e["losses"]
            session.add(wallet)
            added += 1
        session.commit()
    finally:
        session.close()

    return added, skipped


def seed_evm_from_birdeye_top_traders(token_address: str, chain: str = "base"):
    """
    Stub: pull the Top Traders list for a specific high-conviction token from
    Birdeye's API (needs a free-tier key: https://birdeye.so/find-gems).
    This is the closest free substitute for a global EVM leaderboard —
    it's per-token, so run it against tokens you already have conviction on,
    not as a blind discovery tool.
    """
    raise NotImplementedError(
        "Add your Birdeye API key and wire this to "
        "https://public-api.birdeye.so/defi/v2/tokens/top_traders "
        f"— chain={chain}, token={token_address}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed tracked wallets from KOLscan's Solana leaderboard")
    parser.add_argument("--min-trades", type=int, default=5, help="Minimum W+L trades to qualify (filters noise)")
    parser.add_argument("--top", type=int, default=15, help="Max wallets to pull")
    args = parser.parse_args()

    added, skipped = seed_from_kolscan(min_trades=args.min_trades, top=args.top)
    print(f"Added {added} wallet(s), skipped {skipped} already-tracked.")
