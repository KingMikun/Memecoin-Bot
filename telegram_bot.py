from telegram.ext import Application, CommandHandler

from config import TELEGRAM_BOT_TOKEN
from bot.handlers import (
    start, help_cmd, add_wallet, list_wallets, label_wallet, untrack_wallet, stats,
    import_wallets,
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

    return app
