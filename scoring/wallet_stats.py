"""
Turns raw Trade rows into readable wallet history, realized PnL, and win rate.

FIFO-matches trades per (chain, token) on actual *quantity* (token_amount),
pricing each matched slice at its own trade's price (amount_usd / token_amount).
Also carries market cap at the moment of each trade through the match, so a
closed round trip can show "bought at $850K mcap, sold at $2.1M mcap" —
not just a raw dollar PnL.

Known limitations, surfaced in the output rather than hidden:
- If a trade's price couldn't be fetched at ingest time (amount_usd = 0,
  e.g. Robinhood Chain, or a DexScreener miss), that slice is excluded from
  PnL rather than treated as a $0 trade.
- Market cap is whatever DexScreener reported at ingest time for the most
  liquid pair — a snapshot, not a guaranteed-accurate on-chain calculation.
"""
from collections import defaultdict, deque
from dataclasses import dataclass, field

from sqlalchemy import select

from database import Wallet, Trade


@dataclass
class _Lot:
    remaining_qty: float
    price: float
    mcap: float | None
    timestamp: object
    token_symbol: str


@dataclass
class ClosedRoundTrip:
    chain: str
    token_address: str
    token_symbol: str
    qty: float
    cost_basis: float
    proceeds: float
    mcap_bought: float | None
    mcap_sold: float | None
    opened_at: object
    closed_at: object

    @property
    def pnl(self) -> float:
        return round(self.proceeds - self.cost_basis, 2)

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


@dataclass
class WalletStats:
    wallet_rows: list
    trades: list
    closed_trips: list = field(default_factory=list)
    open_positions: dict = field(default_factory=dict)
    unpriced_trades: int = 0

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def realized_pnl(self) -> float:
        return round(sum(t.pnl for t in self.closed_trips), 2)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.closed_trips if t.is_win)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.closed_trips if not t.is_win)

    @property
    def win_rate(self) -> float | None:
        total = self.wins + self.losses
        return round(self.wins / total * 100, 1) if total else None


def get_wallet_stats(session, wallet_rows, trade_limit: int = 500) -> WalletStats:
    wallet_ids = [w.id for w in wallet_rows]
    trades = session.execute(
        select(Trade)
        .where(Trade.wallet_id.in_(wallet_ids))
        .order_by(Trade.timestamp.asc())
        .limit(trade_limit)
    ).scalars().all()

    open_lots = defaultdict(deque)
    closed = []
    unpriced = 0

    for t in trades:
        key = (t.chain, t.token_address)
        qty = t.token_amount or 0
        price = (t.amount_usd / qty) if (qty and t.amount_usd) else None

        if price is None:
            unpriced += 1
            continue

        if t.action == "buy":
            open_lots[key].append(_Lot(
                remaining_qty=qty, price=price, mcap=t.entry_mcap,
                timestamp=t.timestamp, token_symbol=t.token_symbol,
            ))
        elif t.action == "sell":
            remaining_sell_qty = qty
            while remaining_sell_qty > 0 and open_lots[key]:
                lot = open_lots[key][0]
                matched_qty = min(lot.remaining_qty, remaining_sell_qty)
                closed.append(ClosedRoundTrip(
                    chain=t.chain,
                    token_address=t.token_address,
                    token_symbol=t.token_symbol or lot.token_symbol,
                    qty=matched_qty,
                    cost_basis=round(matched_qty * lot.price, 2),
                    proceeds=round(matched_qty * price, 2),
                    mcap_bought=lot.mcap,
                    mcap_sold=t.entry_mcap,
                    opened_at=lot.timestamp,
                    closed_at=t.timestamp,
                ))
                lot.remaining_qty -= matched_qty
                remaining_sell_qty -= matched_qty
                if lot.remaining_qty <= 1e-12:
                    open_lots[key].popleft()

    open_positions = {
        f"{chain}:{token}": sum(l.remaining_qty for l in lots)
        for (chain, token), lots in open_lots.items() if lots
    }

    return WalletStats(
        wallet_rows=wallet_rows,
        trades=list(reversed(trades)),
        closed_trips=closed,
        open_positions=open_positions,
        unpriced_trades=unpriced,
    )


def _fmt_mcap(mcap: float | None) -> str:
    if mcap is None:
        return "mcap n/a"
    if mcap >= 1_000_000:
        return f"${mcap/1_000_000:,.2f}M mcap"
    if mcap >= 1_000:
        return f"${mcap/1_000:,.0f}K mcap"
    return f"${mcap:,.0f} mcap"


def format_wallet_history(stats: WalletStats, recent_n: int = 10, closed_n: int = 5) -> str:
    if not stats.wallet_rows:
        return "Wallet not found."

    w = stats.wallet_rows[0]
    chains_tracked = ", ".join(row.chain.title() for row in stats.wallet_rows)
    label = w.label or w.address[:6] + "..." + w.address[-4:]

    lines = [
        f"*{label}*",
        f"`{w.address}`",
        f"Tracked on: {chains_tracked}\n",
        f"*Total trades:* {stats.total_trades}",
    ]

    if stats.win_rate is not None:
        lines.append(f"*Closed trades win rate:* {stats.win_rate}% ({stats.wins}W / {stats.losses}L)")
        sign = "+" if stats.realized_pnl >= 0 else "-"
        lines.append(f"*Realized PnL:* {sign}${abs(stats.realized_pnl):,.2f}")
    else:
        lines.append("*Closed trades:* none yet — no completed, price-confirmed round trips to score")

    if stats.open_positions:
        lines.append(f"*Open positions:* {len(stats.open_positions)}")

    if stats.unpriced_trades:
        lines.append(f"_{stats.unpriced_trades} trade(s) excluded — price unavailable at ingest time_")

    if stats.closed_trips:
        lines.append("\n*Closed positions (bought mcap → sold mcap):*")
        for ct in sorted(stats.closed_trips, key=lambda c: c.closed_at, reverse=True)[:closed_n]:
            sign = "+" if ct.pnl >= 0 else "-"
            lines.append(
                f"{'🟢' if ct.is_win else '🔴'} `{ct.token_symbol}` ({ct.chain.title()}) — "
                f"{ct.qty:,.4g} tokens — {_fmt_mcap(ct.mcap_bought)} → {_fmt_mcap(ct.mcap_sold)} — "
                f"PnL {sign}${abs(ct.pnl):,.2f}"
            )

    if stats.trades:
        lines.append("\n*Recent trades:*")
        for t in stats.trades[:recent_n]:
            emoji = "🟢" if t.action == "buy" else "🔴"
            when = t.timestamp.strftime("%b %d %H:%M UTC")
            size = f"${t.amount_usd:,.2f}" if t.amount_usd else "size n/a"
            qty = f"{t.token_amount:,.4g}" if t.token_amount else "qty n/a"
            mcap = _fmt_mcap(t.entry_mcap)
            lines.append(
                f"{emoji} {t.action.upper()} `{t.token_symbol or t.token_address[:8]}` "
                f"on {t.chain.title()} — {qty} tokens — {size} — {mcap} — {when}"
            )

    return "\n".join(lines)
