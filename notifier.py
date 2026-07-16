"""
Two kinds of Telegram output, kept deliberately separate:

  1. Trade spotted — fires on EVERY trade from a tracked wallet, buy or sell.
     Just information: who, what, contract address, links. No scoring gate.
  2. Confluence alert — the "this ticks the boxes" high-conviction alert,
     gated on scoring + security passing.
"""
from telegram import Bot
from telegram.constants import ParseMode

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALERT_CHAT_ID
from scoring.confluence import OpportunityScore
from scoring.security import SecurityResult
from utils.chains import helpful_links, wallet_explorer_url

_bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None


def format_trade_notification(
    wallet_label: str, wallet_address: str, chain: str,
    action: str, token_address: str, token_symbol: str, amount_usd: float,
) -> str:
    emoji = "🟢" if action == "buy" else "🔴"
    wallet_link = wallet_explorer_url(chain, wallet_address)
    wallet_display = f"[{wallet_label or wallet_address[:6]}]({wallet_link})" if wallet_link else (wallet_label or wallet_address)

    return (
        f"{emoji} *{action.upper()}* — tracked wallet\n\n"
        f"*Wallet:* {wallet_display}\n"
        f"*Chain:* {chain.title()}\n"
        f"*Token:* `{token_symbol or 'unknown'}`\n"
        f"*Contract:* `{token_address}`\n"
        f"*Size:* ${amount_usd:,.2f}\n"
        f"*Links:* {helpful_links(chain, token_address)}"
    )


async def send_trade_notification(
    wallet_label: str, wallet_address: str, chain: str,
    action: str, token_address: str, token_symbol: str, amount_usd: float,
):
    text = format_trade_notification(
        wallet_label, wallet_address, chain, action, token_address, token_symbol, amount_usd,
    )
    if _bot is None or not TELEGRAM_ALERT_CHAT_ID:
        print("[notifier] Telegram not configured — printing instead:")
        print(text)
        return

    await _bot.send_message(
        chat_id=TELEGRAM_ALERT_CHAT_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


def format_alert(opp: OpportunityScore, sec: SecurityResult, token_symbol: str = "") -> str:
    wallet_lines = "\n".join(
        f"  • {w.label or w.address[:6] + '...' + w.address[-4:]}"
        + (f" ({w.win_rate}% WR)" if w.win_rate is not None else "")
        for w in opp.wallets
    )
    reason_lines = "\n".join(f"  ✓ {r}" for r in opp.reasons)

    return (
        f"🟢 *King Analytics — Confluence Alert*\n\n"
        f"*Token:* `{token_symbol or opp.token_address}`\n"
        f"*Chain:* {opp.chain.title()}\n"
        f"*Contract:* `{opp.token_address}`\n"
        f"*Links:* {helpful_links(opp.chain, opp.token_address)}\n"
        f"*Score:* {opp.score}/100\n\n"
        f"*Tracked wallets in:*\n{wallet_lines}\n\n"
        f"*Why it ticks the boxes:*\n{reason_lines}\n\n"
        f"*Security:* ✅ passed honeypot/rug check\n\n"
        f"_Not financial advice. Size it like you'd hate to lose it._"
    )


async def send_alert(opp: OpportunityScore, sec: SecurityResult, token_symbol: str = ""):
    if _bot is None or not TELEGRAM_ALERT_CHAT_ID:
        print("[alerts] Telegram not configured — printing instead:")
        print(format_alert(opp, sec, token_symbol))
        return

    await _bot.send_message(
        chat_id=TELEGRAM_ALERT_CHAT_ID,
        text=format_alert(opp, sec, token_symbol),
        parse_mode=ParseMode.MARKDOWN,
    )
