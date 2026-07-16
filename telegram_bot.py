import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import TELEGRAM_BOT_TOKEN
from bot.handlers import (
    start, help_cmd, add_wallet, list_wallets, label_wallet, untrack_wallet, stats,
    import_wallets, wallet_history,
)

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """
    Without this, an unhandled exception inside any command handler gets
    logged server-side and the user sees... nothing. No error, no reply,
    just silence — which looks identical to "command not recognized" and
    is impossible to debug from the Telegram side. This makes failures
    visible instead of silent.
    """
    logger.error("Update %s caused error: %s", update, context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ Something went wrong running that command. "
                 "If this keeps happening, it's likely a deploy or DB schema mismatch — check Railway logs.",
        )


def build_application() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set — check your .env")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("addwallet", add_wallet))
    app.add_handler(CommandHandler("importwallets", import_wallets))
    app.add_handler(CommandHandler("wallets", list_wallets))
    app.add_handler(CommandHandler("label", label_wallet))
    app.add_handler(CommandHandler("untrack", untrack_wallet))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("wallethistory", wallet_history))
    app.add_error_handler(error_handler)

    return app
