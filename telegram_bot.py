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

    commands = {
        "start": start,
        "help": help_cmd,
        "addwallet": add_wallet,
        "importwallets": import_wallets,
        "wallets": list_wallets,
        "label": label_wallet,
        "untrack": untrack_wallet,
        "stats": stats,
        "wallethistory": wallet_history,
    }
    for name, handler_fn in commands.items():
        app.add_handler(CommandHandler(name, handler_fn))
    app.add_error_handler(error_handler)

    # Prints unconditionally at every startup, independent of any test command —
    # the fastest possible check for "is this deploy actually current": if a
    # command you expect isn't in this list, the deployed bot/handlers.py or
    # bot/telegram_bot.py is stale, full stop, no further diagnosis needed.
    print(f"[telegram_bot] Registered commands: {', '.join('/' + c for c in commands)}")

    return app
