"""
One job: turn a passed opportunity into a clean, King Analytics-branded
Telegram message and ship it.
"""
from telegram import Bot
from telegram.constants import ParseMode

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALERT_CHAT_ID
from scoring.confluence import OpportunityScore
from scoring.security import SecurityResult

_bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None


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
        f"*Score:* {opp.score}/100\n\n"
        f"*Tracked wallets in:*\n{wallet_lines}\n\n"
        f"*Why it ticks the boxes:*\n{reason_lines}\n\n"
        f"*Security:* ✅ passed honeypot/rug check\n\n"
        f"`{opp.token_address}`\n\n"
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
