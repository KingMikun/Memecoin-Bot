"""
Mcap and FDV say nothing about conviction. Confluence does.

This engine ignores size entirely and scores a token on:
  1. how many tracked wallets bought it in a tight window
  2. the historical win rate of those wallets
  3. liquidity health
  4. holder concentration (pulled in via the security check upstream)

Score is 0-100. Anything below the alert threshold gets logged, not pushed.
"""
from datetime import datetime, timedelta
from sqlalchemy import select

from database import Trade, Wallet, get_session
from config import MIN_CONFLUENCE_WALLETS, CONFLUENCE_WINDOW_MINUTES, MIN_LIQUIDITY_USD


class OpportunityScore:
    def __init__(self, token_address: str, chain: str, score: float,
                 wallet_count: int, wallets: list[Wallet], reasons: list[str]):
        self.token_address = token_address
        self.chain = chain
        self.score = score
        self.wallet_count = wallet_count
        self.wallets = wallets
        self.reasons = reasons

    @property
    def is_alertable(self) -> bool:
        return self.wallet_count >= MIN_CONFLUENCE_WALLETS and self.score >= 60


def score_token(chain: str, token_address: str, liquidity_usd: float | None = None) -> OpportunityScore:
    """Called right after a new trade lands — checks if it now has confluence."""
    session = get_session()
    try:
        window_start = datetime.utcnow() - timedelta(minutes=CONFLUENCE_WINDOW_MINUTES)

        recent_buys = session.execute(
            select(Trade)
            .where(Trade.chain == chain)
            .where(Trade.token_address == token_address)
            .where(Trade.action == "buy")
            .where(Trade.timestamp >= window_start)
        ).scalars().all()

        wallet_ids = {t.wallet_id for t in recent_buys}
        wallets = [session.get(Wallet, wid) for wid in wallet_ids]
        wallets = [w for w in wallets if w is not None]

        reasons = []
        score = 0.0

        # 1. Confluence weight — up to 50 points
        confluence_points = min(len(wallets), 5) * 10
        score += confluence_points
        if wallets:
            reasons.append(f"{len(wallets)} tracked wallet(s) bought in the last {CONFLUENCE_WINDOW_MINUTES}min")

        # 2. Win-rate weight — up to 30 points
        rated = [w for w in wallets if w.win_rate is not None]
        if rated:
            avg_win_rate = sum(w.win_rate for w in rated) / len(rated)
            score += (avg_win_rate / 100) * 30
            reasons.append(f"avg wallet win rate: {avg_win_rate:.0f}%")

        # 3. Liquidity health — up to 20 points
        if liquidity_usd is not None:
            if liquidity_usd >= MIN_LIQUIDITY_USD:
                score += 20
                reasons.append(f"liquidity ${liquidity_usd:,.0f} — healthy")
            else:
                reasons.append(f"liquidity ${liquidity_usd:,.0f} — thin, size down")

        return OpportunityScore(
            token_address=token_address,
            chain=chain,
            score=round(score, 1),
            wallet_count=len(wallets),
            wallets=wallets,
            reasons=reasons,
        )
    finally:
        session.close()
