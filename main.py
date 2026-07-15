"""
Single process, two jobs — matches how King Analytics core already runs on
Railway: one service, one Procfile, one deploy.

- FastAPI serves the Helius + Alchemy webhook endpoints
- python-telegram-bot polls Telegram for commands in the same event loop

Run locally:   uvicorn main:app --reload
Deploy:        Railway auto-detects via Procfile (web: uvicorn main:app --host 0.0.0.0 --port $PORT)
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from database import init_db
from bot.telegram_bot import build_application
from ingest.helius_webhook import router as helius_router
from ingest.evm_webhook import router as evm_router

telegram_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app
    init_db()

    telegram_app = build_application()
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()
    print("[main] Telegram bot polling started, DB ready, webhooks live.")

    yield

    await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()


app = FastAPI(title="King Analytics Wallet Intelligence Bot", lifespan=lifespan)
app.include_router(helius_router)
app.include_router(evm_router)


@app.get("/")
async def health():
    return {"status": "king analytics wallet bot — online"}
