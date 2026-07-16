"""
King Analytics — Wallet Intelligence Bot
Central config. Everything reads from here so you tune once, not five times.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")

# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./king_wallet_bot.db")

# --- Provider keys ---
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_WEBHOOK_SECRET = os.getenv("HELIUS_WEBHOOK_SECRET", "")
HELIUS_WEBHOOK_ID = os.getenv("HELIUS_WEBHOOK_ID", "")  # from creating the webhook once, see README

ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY", "")
ALCHEMY_WEBHOOK_SIGNING_KEY = os.getenv("ALCHEMY_WEBHOOK_SIGNING_KEY", "")
ALCHEMY_AUTH_TOKEN = os.getenv("ALCHEMY_AUTH_TOKEN", "")  # Notify API token, dashboard > Settings > Auth Token
# Alchemy webhooks are per-network, so each chain needs its own webhook ID
ALCHEMY_WEBHOOK_IDS = {
    "ethereum": os.getenv("ALCHEMY_WEBHOOK_ID_ETHEREUM", ""),
    "base": os.getenv("ALCHEMY_WEBHOOK_ID_BASE", ""),
    "robinhood": os.getenv("ALCHEMY_WEBHOOK_ID_ROBINHOOD", ""),
}

GOPLUS_API_KEY = os.getenv("GOPLUS_API_KEY", "")
GOPLUS_API_SECRET = os.getenv("GOPLUS_API_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")

# --- Scoring knobs ---
MIN_CONFLUENCE_WALLETS = int(os.getenv("MIN_CONFLUENCE_WALLETS", 2))
CONFLUENCE_WINDOW_MINUTES = int(os.getenv("CONFLUENCE_WINDOW_MINUTES", 15))
MAX_TOP10_HOLDER_PCT = float(os.getenv("MAX_TOP10_HOLDER_PCT", 60))
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", 3000))

# --- Chains this bot watches ---
# GoPlus chain IDs: https://docs.gopluslabs.io/reference/chainids
CHAINS = {
    "ethereum": {"label": "Ethereum", "goplus_id": "1", "kind": "evm"},
    "base": {"label": "Base", "goplus_id": "8453", "kind": "evm"},
    "robinhood": {"label": "Robinhood Chain", "goplus_id": None, "kind": "evm"},
    # Robinhood Chain launched July 1 2026 on Arbitrum Orbit tech — EVM-compatible,
    # so the same Alchemy/GoPlus pipeline applies once GoPlus adds a chain ID.
    # Until then it falls back to manual contract checks (see scoring/security.py).
    "solana": {"label": "Solana", "goplus_id": None, "kind": "solana"},
}

# A single EVM wallet address is valid on every EVM chain by construction —
# there's no such thing as "this address only exists on Base." So tracking
# a wallet on any one EVM chain tracks it on all of them automatically.
EVM_CHAINS = [c for c, cfg in CHAINS.items() if cfg["kind"] == "evm"]
