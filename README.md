# King Analytics — Wallet Intelligence Bot

Charts show you what already happened. Wallets show you what's about to.

This bot watches labeled wallets across **Ethereum, Solana, Base, and Robinhood Chain**,
flags confluence — multiple smart wallets buying the same token in a tight window —
and only alerts you once the token clears a honeypot/rug check. No mcap or FDV filter.
If it ticks the boxes, it ticks the boxes.

## What it does

1. **Tracks wallets you label** — `/addwallet`, `/wallets`, `/label`, `/untrack`
2. **Ingests trades in real time** via Helius (Solana) and Alchemy (EVM: Ethereum, Base,
   Robinhood Chain) webhooks — push-based, not polling
3. **Scores confluence** — wallet count, historical win rate, liquidity health — size-agnostic
4. **Gates every alert through GoPlus Security** — honeypot, mint/freeze authority, sell tax,
   holder concentration. Fails the check → no alert, full stop.
5. **Pushes a clean, branded alert to Telegram** the moment a token clears both bars

## Why Robinhood Chain is in here

Robinhood Chain went live July 1, 2026 — an Arbitrum-based, EVM-compatible L2.
Early memecoin activity is already showing up on it, and because it's EVM-standard,
it plugs into the same Alchemy/GoPlus pipeline as Ethereum and Base with almost no
extra code. That's the whole point of catching a chain early — the tooling catches up,
you're already positioned.

## Setup

```bash
git clone <this repo>
cd king_wallet_bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
```

### Keys you need
| Service | What for | Get it |
|---|---|---|
| Telegram Bot Token | the bot itself | @BotFather on Telegram |
| Helius API key | Solana webhooks | dev.helius.xyz |
| Alchemy API key | Ethereum/Base/Robinhood Chain webhooks | alchemy.com |
| GoPlus API key + secret | honeypot/rug checks | gopluslabs.io (free tier available) |

### Run locally
```bash
uvicorn main:app --reload
```
Talk to your bot on Telegram, run `/addwallet <address> <chain> <label>`.

### Deploy to Railway
1. Push this repo to GitHub, connect it to a new Railway project
2. Add a Postgres plugin — Railway injects `DATABASE_URL` automatically, the bot
   upgrades from SQLite to Postgres with zero code changes
3. Set the env vars from `.env.example` in Railway's dashboard
4. Set `PUBLIC_BASE_URL` to the Railway-assigned domain
5. Deploy — Railway reads the `Procfile` automatically

### Wiring up webhooks live
When you `/addwallet`, the bot stores it locally but does **not** yet auto-register it
with Helius/Alchemy — that's the one manual step left as a TODO in
`bot/handlers.py::add_wallet()`. Two ways to close it:
- Quick: add the address manually in the Helius/Alchemy dashboard after each `/addwallet`
- Full auto: call `helius.webhooks.updateWebhook()` / Alchemy's Notify API inside
  `add_wallet()` — both are straightforward REST PATCH calls, ~15 lines each

## Project structure
```
main.py                 FastAPI + Telegram bot, single-process entrypoint
config.py                all env vars and chain config in one place
database.py              Wallet / Trade / Alert / Subscriber models
bot/handlers.py          /addwallet /wallets /label /untrack /stats
ingest/helius_webhook.py Solana trade ingestion
ingest/evm_webhook.py    Ethereum / Base / Robinhood Chain trade ingestion
scoring/confluence.py    size-agnostic opportunity scoring
scoring/security.py      GoPlus honeypot/rug gate
alerts/notifier.py       formats + sends the Telegram alert
```

## Tuning the filter
Everything's in `.env` — confluence wallet minimum, time window, max holder
concentration, min liquidity. No mcap/FDV cutoff by design; if you want one later,
add it in `scoring/confluence.py::score_token()`.

## Not financial advice
The bot flags patterns, not guarantees. GoPlus and every honeypot detector will
occasionally miss something new — treat every alert as a lead to verify, not an
auto-buy signal.
