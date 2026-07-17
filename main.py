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

from fastapi import FastAPI, Request
from telegram import Update

from config import PUBLIC_BASE_URL
from database import init_db
from bot.telegram_bot import build_application
from ingest.helius_webhook import router as helius_router
from ingest.evm_webhook import router as evm_router

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
        await telegram_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
        print(f"[main] Telegram webhook set to {webhook_url} — DB ready, chain webhooks live.")
    else:
        await telegram_app.updater.start_polling()
        print("[main] PUBLIC_BASE_URL not set — falling back to polling (local dev). DB ready, chain webhooks live.")

    yield

    if PUBLIC_BASE_URL:
        await telegram_app.bot.delete_webhook()
    else:
        await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()


app = FastAPI(title="King Analytics Wallet Intelligence Bot", lifespan=lifespan)
app.include_router(helius_router)
app.include_router(evm_router)


@app.post(TELEGRAM_WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
async def health():
    return {"status": "king analytics wallet bot — online"}
