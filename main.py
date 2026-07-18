"""
Single process, two jobs — matches how King Analytics core already runs on
Railway: one service, one Procfile, one deploy.

- FastAPI serves the Helius + Alchemy webhook endpoints
- Telegram updates arrive via webhook (production) or polling (local dev)

Why webhook instead of polling in production: Telegram only allows ONE
process to hold a getUpdates long-poll connection per bot token. Any overlap
during a deploy (old container not fully stopped, a second replica, a local
test run left running) throws telegram.error.Conflict and crash-loops the
whole app — which is exactly what silently ate commands before this change.
A webhook has no such contention: Telegram just POSTs to a URL, and
whichever container is up handles it. Falls back to polling automatically
when PUBLIC_BASE_URL isn't set (e.g. running locally with `uvicorn --reload`),
since a webhook needs a real public URL to register.

Run locally:   uvicorn main:app --reload            (uses polling)
Deploy:        Railway auto-detects via Procfile     (uses webhook)
"""
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from telegram import Update

from config import PUBLIC_BASE_URL
from database import init_db
from bot.telegram_bot import build_application
from ingest.helius_webhook import router as helius_router
from ingest.evm_webhook import router as evm_router

# Without this, logger.info() calls are silently dropped — Python's default
# logging level only surfaces WARNING and above, which is why "[main]" print()
# statements were visible in Railway logs but any logger.info() call never was.
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

logger = logging.getLogger(__name__)

telegram_app = None
TELEGRAM_WEBHOOK_PATH = "/webhook/telegram"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app
    init_db()

    telegram_app = build_application()
    await telegram_app.initialize()
    await telegram_app.start()

    if PUBLIC_BASE_URL:
        webhook_url = f"{PUBLIC_BASE_URL.rstrip('/')}{TELEGRAM_WEBHOOK_PATH}"
        try:
            ok = await telegram_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
            info = await telegram_app.bot.get_webhook_info()
            if ok:
                print(f"[main] Telegram webhook set to {webhook_url} — confirmed url={info.url}")
            else:
                logger.error(f"[main] set_webhook returned False for {webhook_url} — Telegram rejected it silently")
        except Exception:
            # A failed webhook registration must NOT take down the chain-trade
            # webhooks or the whole app with it — log it loudly and keep going.
            # Telegram commands will be dead until this is fixed, but Helius/
            # Alchemy ingestion and confluence alerts stay alive.
            logger.exception(f"[main] Failed to set Telegram webhook to {webhook_url} — Telegram commands are DOWN until this is fixed. Chain webhooks remain live.")
    else:
        await telegram_app.updater.start_polling()
        print("[main] PUBLIC_BASE_URL not set — falling back to polling (local dev). DB ready, chain webhooks live.")

    yield

    # Deliberately NOT calling delete_webhook() here. Telegram webhooks persist
    # fine across restarts — there's no need to tear one down on shutdown, and
    # doing so is actively harmful with Railway's rolling deploys: an old
    # container can still be winding down in the background after a new one
    # has already started and re-registered the webhook, and that old
    # container's shutdown would wipe out what the new one just set. Letting
    # the next startup's set_webhook() call (idempotent) be the only thing
    # that touches it avoids that race entirely.
    if not PUBLIC_BASE_URL:
        await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()


app = FastAPI(title="King Analytics Wallet Intelligence Bot", lifespan=lifespan)
app.include_router(helius_router)
app.include_router(evm_router)


@app.post(TELEGRAM_WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    if telegram_app is None:
        logger.error("[telegram_webhook] Hit before app finished starting up")
        return {"ok": False}
    data = await request.json()
    # Log receipt unconditionally, before any handler logic runs — this is
    # the one log line that proves Telegram actually delivered the update,
    # independent of whether a command handler works, crashes, or is missing.
    update_id = data.get("update_id")
    text = data.get("message", {}).get("text", "")
    logger.info(f"[telegram_webhook] Received update_id={update_id} text={text!r}")

    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    logger.info(f"[telegram_webhook] Finished processing update_id={update_id}")
    return {"ok": True}


@app.get("/")
async def health():
    webhook_status = "n/a (polling mode)"
    if telegram_app is not None and PUBLIC_BASE_URL:
        try:
            info = await telegram_app.bot.get_webhook_info()
            webhook_status = {"url": info.url, "pending_update_count": info.pending_update_count, "last_error": info.last_error_message}
        except Exception as e:
            webhook_status = f"error checking webhook: {e}"
    return {"status": "king analytics wallet bot — online", "telegram_webhook": webhook_status}
